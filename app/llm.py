import json
import math
from typing import Dict, List, Optional, Union, Any
from pathlib import Path
import tempfile
import os
import base64
import mimetypes

from PIL import Image
import tiktoken
from openai import (
    APIError,
    AsyncAzureOpenAI,
    AsyncOpenAI,
    AuthenticationError,
    InternalServerError as _OAIInternalServerError,
    OpenAIError,
    RateLimitError,
)
from openai.types.chat import ChatCompletion, ChatCompletionMessage
from tenacity import (
    retry,
    retry_if_exception,
    retry_if_exception_type,
    stop_after_attempt,
    wait_random_exponential,
)

from app.bedrock import BedrockClient
from app.config import LLMSettings, config
from app.exceptions import TokenLimitExceeded


def _should_retry_llm_error(exc: BaseException) -> bool:
    """Tenacity retry predicate: return True only for errors worth retrying.

    429 Rate-limit and transient 500 errors → retry with backoff.
    529 Overloaded (Anthropic-specific) → fail fast; retrying makes overload worse.
    TokenLimitExceeded → never retry (context cannot shrink on its own).
    """
    if isinstance(exc, TokenLimitExceeded):
        return False
    # 529 == Anthropic overloaded — fail immediately so callers degrade gracefully.
    if isinstance(exc, _OAIInternalServerError) and getattr(exc, "status_code", None) == 529:
        return False
    return isinstance(exc, (OpenAIError, ValueError))
from app.logger import logger  # Assuming a logger is set up in your app
from app.schema import (
    ROLE_VALUES,
    TOOL_CHOICE_TYPE,
    TOOL_CHOICE_VALUES,
    Message,
    ToolChoice,
)


REASONING_MODELS = ["o1", "o3-mini", "deepseek-r1-250528"]
MULTIMODAL_MODELS = [
    "gpt-4-vision-preview",
    "gpt-4o",
    "gpt-4o-mini",
    "claude-3-5-sonnet-20241022",
    "claude-3-opus-20240229",
    "claude-3-sonnet-20240229",
    "claude-3-haiku-20240307",
    "gemini-2.0-flash",
    "gemini-2.5-pro",
    "qwen2.5-vl-72b-instruct",
    "claude-3-7-sonnet-20250219",
    "gemini-2.5-flash-preview-05-20",
    "glm-4v-flash",
    "GLM-4.1V-Thinking-Flash"
]


class TokenCounter:
    # Token constants
    BASE_MESSAGE_TOKENS = 4
    FORMAT_TOKENS = 2
    LOW_DETAIL_IMAGE_TOKENS = 85
    HIGH_DETAIL_TILE_TOKENS = 170

    # Image processing constants
    MAX_SIZE = 2048
    HIGH_DETAIL_TARGET_SHORT_SIDE = 768
    TILE_SIZE = 512

    def __init__(self, tokenizer):
        self.tokenizer = tokenizer

    def count_text(self, text: str) -> int:
        """Calculate tokens for a text string"""
        return 0 if not text else len(self.tokenizer.encode(text))

    def count_image(self, image_item: dict) -> int:
        """
        Calculate tokens for an image based on detail level and dimensions

        For "low" detail: fixed 85 tokens
        For "high" detail:
        1. Scale to fit in 2048x2048 square
        2. Scale shortest side to 768px
        3. Count 512px tiles (170 tokens each)
        4. Add 85 tokens
        """
        detail = image_item.get("detail", "medium")

        # For low detail, always return fixed token count
        if detail == "low":
            return self.LOW_DETAIL_IMAGE_TOKENS

        # For medium detail (default in OpenAI), use high detail calculation
        # OpenAI doesn't specify a separate calculation for medium

        # For high detail, calculate based on dimensions if available
        if detail == "high" or detail == "medium":
            # If dimensions are provided in the image_item
            if "dimensions" in image_item:
                width, height = image_item["dimensions"]
                return self._calculate_high_detail_tokens(width, height)

        return (
            self._calculate_high_detail_tokens(1024, 1024) if detail == "high" else 1024
        )

    def _calculate_high_detail_tokens(self, width: int, height: int) -> int:
        """Calculate tokens for high detail images based on dimensions"""
        # Step 1: Scale to fit in MAX_SIZE x MAX_SIZE square
        if width > self.MAX_SIZE or height > self.MAX_SIZE:
            scale = self.MAX_SIZE / max(width, height)
            width = int(width * scale)
            height = int(height * scale)

        # Step 2: Scale so shortest side is HIGH_DETAIL_TARGET_SHORT_SIDE
        scale = self.HIGH_DETAIL_TARGET_SHORT_SIDE / min(width, height)
        scaled_width = int(width * scale)
        scaled_height = int(height * scale)

        # Step 3: Count number of 512px tiles
        tiles_x = math.ceil(scaled_width / self.TILE_SIZE)
        tiles_y = math.ceil(scaled_height / self.TILE_SIZE)
        total_tiles = tiles_x * tiles_y

        # Step 4: Calculate final token count
        return (
            total_tiles * self.HIGH_DETAIL_TILE_TOKENS
        ) + self.LOW_DETAIL_IMAGE_TOKENS

    def count_content(self, content: Union[str, List[Union[str, dict]]]) -> int:
        """Calculate tokens for message content"""
        if not content:
            return 0

        if isinstance(content, str):
            return self.count_text(content)

        token_count = 0
        for item in content:
            if isinstance(item, str):
                token_count += self.count_text(item)
            elif isinstance(item, dict):
                if "text" in item:
                    token_count += self.count_text(item["text"])
                elif "image_url" in item:
                    token_count += self.count_image(item)
        return token_count

    def count_tool_calls(self, tool_calls: List[dict]) -> int:
        """Calculate tokens for tool calls"""
        token_count = 0
        for tool_call in tool_calls:
            if "function" in tool_call:
                function = tool_call["function"]
                token_count += self.count_text(function.get("name", ""))
                token_count += self.count_text(function.get("arguments", ""))
        return token_count

    def count_message_tokens(self, messages: List[dict]) -> int:
        """Calculate the total number of tokens in a message list"""
        total_tokens = self.FORMAT_TOKENS  # Base format tokens

        for message in messages:
            tokens = self.BASE_MESSAGE_TOKENS  # Base tokens per message

            # Add role tokens
            tokens += self.count_text(message.get("role", ""))

            # Add content tokens
            if "content" in message:
                tokens += self.count_content(message["content"])

            # Add tool calls tokens
            if "tool_calls" in message:
                tokens += self.count_tool_calls(message["tool_calls"])

            # Add name and tool_call_id tokens
            tokens += self.count_text(message.get("name", ""))
            tokens += self.count_text(message.get("tool_call_id", ""))

            total_tokens += tokens

        return total_tokens


class LLM:
    _instances: Dict[str, "LLM"] = {}

    def __new__(
        cls, config_name: str = "default", llm_config: Optional[LLMSettings] = None
    ):
        if config_name not in cls._instances:
            instance = super().__new__(cls)
            instance.__init__(config_name, llm_config)
            cls._instances[config_name] = instance
        return cls._instances[config_name]

    def __init__(
        self, config_name: str = "default", llm_config: Optional[LLMSettings] = None
    ):
        if not hasattr(self, "client"):  # Only initialize if not already initialized
            llm_config = llm_config or config.llm
            llm_config = llm_config.get(config_name, llm_config["default"])
            self.model = llm_config.model
            self.max_tokens = llm_config.max_tokens
            self.temperature = llm_config.temperature
            self.api_type = llm_config.api_type
            self.api_key = llm_config.api_key
            self.api_version = llm_config.api_version
            self.base_url = llm_config.base_url
            self.user_id: Optional[str] = None
            self.db: Optional[Any] = None

            # Add token counting related attributes
            self.total_input_tokens = 0
            self.total_completion_tokens = 0
            self.max_input_tokens = (
                llm_config.max_input_tokens
                if hasattr(llm_config, "max_input_tokens")
                else None
            )

            # Initialize tokenizer
            try:
                self.tokenizer = tiktoken.encoding_for_model(self.model)
            except KeyError:
                # If the model is not in tiktoken's presets, use cl100k_base as default
                self.tokenizer = tiktoken.get_encoding("cl100k_base")

            if self.api_type == "azure":
                self.client = AsyncAzureOpenAI(
                    base_url=self.base_url,
                    api_key=self.api_key,
                    api_version=self.api_version,
                )
            elif self.api_type == "aws":
                self.client = BedrockClient()
            else:
                self.client = AsyncOpenAI(api_key=self.api_key, base_url=self.base_url)

            self.token_counter = TokenCounter(self.tokenizer)

            self.available_models = ["deepseek-r1-250528"]

    def get_context_window(self) -> int:
        """Return the model's approximate context window.

        Defaults to 200k which matches most modern frontier models; override here per model if needed.
        """
        # Known context windows (best-effort). Extend as you add models.
        model_context_overrides: Dict[str, int] = {
            "gpt-4o": 200_000,
            "gpt-4o-mini": 200_000,
            "gpt-4-vision-preview": 128_000,
            "claude-3-5-sonnet-20241022": 200_000,
            "claude-3-7-sonnet-20250219": 200_000,
            "claude-3-opus-20240229": 200_000,
            "claude-3-sonnet-20240229": 200_000,
            "claude-3-haiku-20240307": 200_000,
            "gemini-2.5-pro": 100_000,
            "gemini-2.5-flash-preview-05-20": 100_000,
            "gemini-2.0-flash": 100_000,
            "qwen2.5-vl-72b-instruct": 128_000,
            "glm-4v-flash": 128_000,
            "GLM-4.1V-Thinking-Flash": 128_000,
            "o1": 200_000,
            "o3-mini": 200_000,
        }

        # Exact match first
        if self.model in model_context_overrides:
            return model_context_overrides[self.model]
        # Prefix match fallback
        for key, value in model_context_overrides.items():
            if self.model.startswith(key):
                return value
        return 200_000

    def _truncate_messages_to_token_limit(
        self,
        messages: List[dict],
        max_input_tokens: int,
    ) -> List[dict]:
        """Trim conversation history from the oldest messages until within the token budget.

        - Preserves the newest messages preferentially
        - Keeps at most the most recent system message (if any) at the front
        """
        if not messages:
            return messages

        # Separate out system messages and others
        system_messages: List[dict] = [m for m in messages if m.get("role") == "system"]
        non_system_messages: List[dict] = [m for m in messages if m.get("role") != "system"]

        preserved_system: List[dict] = system_messages[-1:]  # keep only the latest system

        # Accumulate from the end (newest first)
        kept: List[dict] = []
        running = self.token_counter.count_message_tokens(preserved_system)
        for msg in reversed(non_system_messages):
            msg_tokens = self.token_counter.count_message_tokens([msg])
            if running + msg_tokens <= max_input_tokens:
                kept.append(msg)
                running += msg_tokens
            else:
                # If nothing has been kept yet, hard-truncate this single oversized message's content (text only)
                if not kept:
                    truncated_msg = dict(msg)
                    content = truncated_msg.get("content")
                    if isinstance(content, str):
                        # Truncate text content to fit roughly into remaining budget
                        # Reserve a small buffer of 256 tokens
                        allowance = max(0, max_input_tokens - running - 256)
                        if allowance > 0:
                            tokens = self.tokenizer.encode(content)
                            truncated_msg["content"] = self.tokenizer.decode(tokens[-allowance:])
                            if self.token_counter.count_message_tokens(preserved_system + [truncated_msg]) <= max_input_tokens:
                                kept.append(truncated_msg)
                                running = self.token_counter.count_message_tokens(preserved_system + kept[::-1])
                break

        kept.reverse()
        return preserved_system + kept

    def set_user_context(self, user_id: str, db: Any):
        """Sets the user context for token usage tracking."""
        self.user_id = user_id
        self.db = db

    def count_tokens(self, text: str) -> int:
        """Calculate the number of tokens in a text"""
        if not text:
            return 0
        return len(self.tokenizer.encode(text))

    def count_message_tokens(self, messages: List[dict]) -> int:
        return self.token_counter.count_message_tokens(messages)

    def update_token_count(self, input_tokens: int, completion_tokens: int = 0, model_name: Optional[str] = None) -> None:
        """Update token counts"""
        # Only track tokens if max_input_tokens is set
        self.total_input_tokens += input_tokens
        self.total_completion_tokens += completion_tokens
        logger.debug(
            f"Token usage: Input={input_tokens}, Completion={completion_tokens}, "
            f"Cumulative Input={self.total_input_tokens}, Cumulative Completion={self.total_completion_tokens}, "
            f"Total={input_tokens + completion_tokens}, Cumulative Total={self.total_input_tokens + self.total_completion_tokens}"
        )

        if self.db and self.user_id and model_name:
            try:
                self.db.record_token_usage(
                    user_id=self.user_id,
                    model_name=model_name,
                    input_tokens=input_tokens,
                    output_tokens=completion_tokens,
                    total_tokens=input_tokens + completion_tokens,
                )
            except Exception as e:
                logger.error(f"Failed to record token usage in DB: {e}", exc_info=True)

    def check_token_limit(self, input_tokens: int) -> bool:
        """Check if token limits are exceeded"""
        if self.max_input_tokens is not None:
            return (self.total_input_tokens + input_tokens) <= self.max_input_tokens
        # If max_input_tokens is not set, always return True
        return True

    def get_limit_error_message(self, input_tokens: int) -> str:
        """Generate error message for token limit exceeded"""
        if (
            self.max_input_tokens is not None
            and (self.total_input_tokens + input_tokens) > self.max_input_tokens
        ):
            return f"Request may exceed input token limit (Current: {self.total_input_tokens}, Needed: {input_tokens}, Max: {self.max_input_tokens})"

        return "Token limit exceeded"

    @staticmethod
    def format_messages(
        messages: List[Union[dict, Message]], supports_images: bool = False, model_in_use: str = ""
    ) -> List[dict]:
        """
        Format messages for LLM by converting them to OpenAI message format.

        Args:
            messages: List of messages that can be either dict or Message objects
            supports_images: Flag indicating if the target model supports image inputs

        Returns:
            List[dict]: List of formatted messages in OpenAI format

        Raises:
            ValueError: If messages are invalid or missing required fields
            TypeError: If unsupported message types are provided

        Examples:
            >>> msgs = [
            ...     Message.system_message("You are a helpful assistant"),
            ...     {"role": "user", "content": "Hello"},
            ...     Message.user_message("How are you?")
            ... ]
            >>> formatted = LLM.format_messages(msgs)
        """
        formatted_messages = []

        for message in messages:
            # Convert Message objects to dictionaries
            if isinstance(message, Message):
                if model_in_use.startswith("glm"):
                    message = message.to_dict(force_nothink=True)
                else:
                    message = message.to_dict()

            if isinstance(message, dict):
                # If message is a dict, ensure it has required fields
                if "role" not in message:
                    raise ValueError("Message dict must contain 'role' field")

                # Process base64 images if present and model supports images
                if supports_images and message.get("base64_image"):
                    # Initialize or convert content to appropriate format
                    if not message.get("content"):
                        message["content"] = []
                    elif isinstance(message["content"], str):
                        message["content"] = [
                            {"type": "text", "text": message["content"]}
                        ]
                    elif isinstance(message["content"], list):
                        # Convert string items to proper text objects
                        message["content"] = [
                            (
                                {"type": "text", "text": item}
                                if isinstance(item, str)
                                else item
                            )
                            for item in message["content"]
                        ]

                    # Add the image to content
                    message["content"].append(
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{message['base64_image']}"
                            },
                        }# type: ignore
                    )

                    # Remove the base64_image field
                    del message["base64_image"]
                # If model doesn't support images but message has base64_image, handle gracefully
                elif not supports_images and message.get("base64_image"):
                    # Just remove the base64_image field and keep the text content
                    del message["base64_image"]

                if "content" in message or "tool_calls" in message:
                    formatted_messages.append(message)
                # else: do not include the message
            else:
                raise TypeError(f"Unsupported message type: {type(message)}")

        # Validate all messages have required fields
        for msg in formatted_messages:
            if msg["role"] not in ROLE_VALUES:
                raise ValueError(f"Invalid role: {msg['role']}")

        return formatted_messages

    @retry(
        wait=wait_random_exponential(min=1, max=60),
        stop=stop_after_attempt(6),
        retry=retry_if_exception(_should_retry_llm_error),
    )
    async def ask(
        self,
        messages: List[Union[dict, Message]],
        system_msgs: Optional[List[Union[dict, Message]]] = None,
        # stream: bool = True,
        stream: bool = False,
        temperature: Optional[float] = None,
        new_model: Optional[str] = None,
        force_json: bool = False,
    ) -> str:
        """
        Send a prompt to the LLM and get the response.

        Args:
            messages: List of conversation messages
            system_msgs: Optional system messages to prepend
            stream (bool): Whether to stream the response
            temperature (float): Sampling temperature for the response

        Returns:
            str: The generated response

        Raises:
            TokenLimitExceeded: If token limits are exceeded
            ValueError: If messages are invalid or response is empty
            OpenAIError: If API call fails after retries
            Exception: For unexpected errors
        """
        try:
            # Check if the model supports images
            model_in_use = new_model if new_model else self.model
            supports_images = model_in_use in MULTIMODAL_MODELS

            # Format system and user messages with image support check
            if system_msgs:
                system_msgs = self.format_messages(system_msgs, supports_images, model_in_use)
                messages = system_msgs + self.format_messages(messages, supports_images, model_in_use)
            else:
                messages = self.format_messages(messages, supports_images, model_in_use)

            # ── LLM Response Cache ───────────────────────────────────────────
            from app.llm_cache import (
                get_cached_ask, store_cached_ask,
                is_cache_enabled, is_cache_bypass,
            )
            _cache_active = is_cache_enabled() and not is_cache_bypass() and not stream
            if _cache_active:
                _cached = get_cached_ask(model_in_use, messages)
                if _cached is not None:
                    return _cached
            # ────────────────────────────────────────────────────────────────

            # Calculate input token count and enforce context window budget
            input_tokens = self.count_message_tokens(messages)
            context_window = self.get_context_window()

            # Check if token limits are exceeded
            if not self.check_token_limit(input_tokens):
                error_message = self.get_limit_error_message(input_tokens)
                # Raise a special exception that won't be retried
                raise TokenLimitExceeded(error_message)

            params = {
                "model": model_in_use,
                "messages": messages,
            }

            if force_json:
                params["response_format"] = {"type": "json_object"}

            if model_in_use in REASONING_MODELS:
                params["max_completion_tokens"] = self.max_tokens
            else:
                params["max_tokens"] = self.max_tokens
                params["temperature"] = (
                    temperature if temperature is not None else self.temperature
                )

            # Ensure prompt fits within context window before sending
            completion_budget = self.max_tokens if model_in_use not in REASONING_MODELS else self.max_tokens
            safety_buffer = 500
            max_prompt_tokens = max(1024, context_window - completion_budget - safety_buffer)
            if input_tokens > max_prompt_tokens:
                logger.warning(
                    f"Input tokens {input_tokens} exceed max prompt budget {max_prompt_tokens}. Truncating conversation."
                )
                # messages = self._truncate_messages_to_token_limit(messages, max_prompt_tokens)
                input_tokens = self.count_message_tokens(messages)
                params["messages"] = messages

            from app.utils.debug_logger import llm_trace, truncate_msg_content
            # Log last 6 messages (truncated) to avoid O(n²) growth
            tail_msgs = messages[-6:] if len(messages) > 6 else messages
            llm_trace(
                f"{'='*80}\n[ASK INPUT] model={model_in_use} total_msgs={len(messages)} (showing last {len(tail_msgs)})\n"
                + "\n".join(
                    f"[{i}] role={m.get('role','?')} : {json.dumps(truncate_msg_content(m), ensure_ascii=False)}"
                    for i, m in enumerate(tail_msgs)
                )
                + f"\n{'='*80}"
            )
            if not stream:
                # Non-streaming request
                response = await self.client.chat.completions.create(
                    **params, stream=False
                )
                # if not response.choices or not response.choices[0].message.content:
                #     raise ValueError("Empty or invalid response from LLM")

                if not response.choices:
                    raise ValueError("Empty or invalid response from LLM")

                # Update token counts
                self.update_token_count(
                    response.usage.prompt_tokens, response.usage.completion_tokens, model_in_use
                )

                llm_trace(
                    f"[ASK OUTPUT] model={model_in_use}\n{response.choices[0].message.content}\n{'='*80}\n"
                    f"[TOKENS] model={model_in_use} "
                    f"prompt={response.usage.prompt_tokens} completion={response.usage.completion_tokens} "
                    f"total={response.usage.total_tokens}"
                )
                _result = response.choices[0].message.content
                if _cache_active and _result:
                    store_cached_ask(model_in_use, messages, _result)
                return _result

            # Streaming request, For streaming, update estimated token count before making the request
            self.update_token_count(input_tokens, 0, model_in_use)

            response = await self.client.chat.completions.create(**params, stream=True)

            collected_messages = []
            completion_text = ""
            async for chunk in response:
                chunk_message = chunk.choices[0].delta.content or ""
                collected_messages.append(chunk_message)
                completion_text += chunk_message
                print(chunk_message, end="", flush=True)

            print()  # Newline after streaming
            full_response = "".join(collected_messages).strip()
            if not full_response:
                raise ValueError("Empty response from streaming LLM")

            # estimate completion tokens for streaming response
            completion_tokens = self.count_tokens(completion_text)
            logger.debug(
                f"Estimated completion tokens for streaming response: {completion_tokens}"
            )
            self.update_token_count(0, completion_tokens, model_in_use)

            return full_response

        except TokenLimitExceeded:
            # Re-raise token limit errors without logging
            raise
        except ValueError:
            logger.exception(f"Validation error")
            raise
        except OpenAIError as oe:
            logger.error(f"Error found for llm {self.model} in ask")
            logger.exception(f"OpenAI API error")
            if isinstance(oe, AuthenticationError):
                logger.error("Authentication failed. Check API key.")
            elif isinstance(oe, RateLimitError):
                logger.error("Rate limit exceeded. Consider increasing retry attempts.")
            elif isinstance(oe, APIError):
                logger.error(f"API error: {oe}")
            raise
        except Exception:
            logger.exception(f"Unexpected error in ask")
            raise

    async def ask_stream(
        self,
        messages: List[Union[dict, Message]],
        system_msgs: Optional[List[Union[dict, Message]]] = None,
        temperature: Optional[float] = None,
        new_model: Optional[str] = None,
    ):
        """
        Send a prompt to the LLM and stream the response as an async generator.

        Args:
            messages: List of conversation messages
            system_msgs: Optional system messages to prepend
            temperature (float): Sampling temperature for the response
            new_model (str): Optional model override

        Yields:
            str: Individual chunks of the generated response

        Raises:
            TokenLimitExceeded: If token limits are exceeded
            ValueError: If messages are invalid
            OpenAIError: If API call fails
            Exception: For unexpected errors
        """
        try:
            # Check if the model supports images
            model_in_use = new_model if new_model else self.model
            supports_images = model_in_use in MULTIMODAL_MODELS

            # Format system and user messages with image support check
            if system_msgs:
                system_msgs = self.format_messages(system_msgs, supports_images)
                messages = system_msgs + self.format_messages(messages, supports_images)
            else:
                messages = self.format_messages(messages, supports_images)

            # Calculate input token count and enforce context window budget
            input_tokens = self.count_message_tokens(messages)
            context_window = self.get_context_window()

            # Check if token limits are exceeded
            if not self.check_token_limit(input_tokens):
                error_message = self.get_limit_error_message(input_tokens)
                raise TokenLimitExceeded(error_message)

            params = {
                "model": model_in_use,
                "messages": messages,
                "stream": True,
            }

            if model_in_use in REASONING_MODELS:
                params["max_completion_tokens"] = self.max_tokens
            else:
                params["max_tokens"] = self.max_tokens
                params["temperature"] = (
                    temperature if temperature is not None else self.temperature
                )

            # Ensure prompt fits within context window before sending
            completion_budget = self.max_tokens if model_in_use not in REASONING_MODELS else self.max_tokens
            safety_buffer = 500
            max_prompt_tokens = max(1024, context_window - completion_budget - safety_buffer)
            if input_tokens > max_prompt_tokens:
                logger.warning(
                    f"Input tokens {input_tokens} exceed max prompt budget {max_prompt_tokens}. Truncating conversation."
                )
                input_tokens = self.count_message_tokens(messages)
                params["messages"] = messages

            # Update estimated input token count
            self.update_token_count(input_tokens, 0, model_in_use)

            # Make streaming request
            response = await self.client.chat.completions.create(**params)

            completion_text = ""
            async for chunk in response:
                chunk_message = chunk.choices[0].delta.content or ""
                if chunk_message:
                    completion_text += chunk_message
                    yield chunk_message

            # Update completion token count
            completion_tokens = self.count_tokens(completion_text)
            self.update_token_count(0, completion_tokens, model_in_use)

        except TokenLimitExceeded:
            # Re-raise token limit errors without logging
            raise
        except ValueError:
            logger.exception(f"Validation error in ask_stream")
            raise
        except OpenAIError as oe:
            logger.exception(f"OpenAI API error in ask_stream")
            if isinstance(oe, AuthenticationError):
                logger.error("Authentication failed. Check API key.")
            elif isinstance(oe, RateLimitError):
                logger.error("Rate limit exceeded. Consider increasing retry attempts.")
            elif isinstance(oe, APIError):
                logger.error(f"API error: {oe}")
            raise
        except Exception:
            logger.exception(f"Unexpected error in ask_stream")
            raise

    @retry(
        wait=wait_random_exponential(min=1, max=60),
        stop=stop_after_attempt(6),
        retry=retry_if_exception(_should_retry_llm_error),
    )
    async def ask_with_images(
        self,
        messages: List[Union[dict, Message]],
        images: List[Union[str, dict]] = [],
        system_msgs: Optional[List[Union[dict, Message]]] = None,
        stream: bool = False,
        temperature: Optional[float] = None,
        image_paths: Optional[List[str]] = None,
    ) -> str:
        """
        Send a prompt with images to the LLM and get the response.

        Args:
            messages: List of conversation messages
            images: List of image URLs or image data dictionaries
            system_msgs: Optional system messages to prepend
            stream (bool): Whether to stream the response
            temperature (float): Sampling temperature for the response
            image_paths: Optional list of local image file paths (for BigModel compatibility)

        Returns:
            str: The generated response

        Raises:
            TokenLimitExceeded: If token limits are exceeded
            ValueError: If messages are invalid or response is empty
            OpenAIError: If API call fails after retries
            Exception: For unexpected errors
        """
        try:
            # For ask_with_images, we always set supports_images to True because
            # this method should only be called with models that support images
            if self.model not in MULTIMODAL_MODELS:
                raise ValueError(
                    f"Model {self.model} does not support images. Use a model from {MULTIMODAL_MODELS}"
                )

            # Format messages with image support
            formatted_messages = self.format_messages(messages, supports_images=True)

            # Ensure the last message is from the user to attach images
            if not formatted_messages or formatted_messages[-1]["role"] != "user":
                raise ValueError(
                    "The last message must be from the user to attach images"
                )

            # Process the last user message to include images
            last_message = formatted_messages[-1]

            # Convert content to multimodal format if needed
            content = last_message["content"]
            multimodal_content = (
                [{"type": "text", "text": content}]
                if isinstance(content, str)
                else content
                if isinstance(content, list)
                else []
            )

            # Add images to content
            for image in images:
                if isinstance(image, str):
                    multimodal_content.append(
                        {"type": "image_url", "image_url": {"url": image}}
                    )
                elif isinstance(image, dict) and "url" in image:
                    multimodal_content.append({"type": "image_url", "image_url": image})
                elif isinstance(image, dict) and "image_url" in image:
                    multimodal_content.append(image)
                else:
                    raise ValueError(f"Unsupported image format: {image}")
            # Add image_paths if provided (for compatibility)
            if image_paths:
                for image_path in image_paths:
                    # For standard APIs, convert local paths to base64 data URIs
                    img_format = self.detect_image_format(image_path)
                    with open(image_path, "rb") as f:
                        encoded = base64.b64encode(f.read()).decode("utf-8")
                        data_uri = f"data:{img_format};base64,{encoded}"
                        multimodal_content.append(
                            {"type": "image_url", "image_url": {"url": data_uri}} # type: ignore
                        )
            # Update the message with multimodal content
            last_message["content"] = multimodal_content

            # Add system messages if provided
            if system_msgs:
                all_messages = (
                    self.format_messages(system_msgs, supports_images=True)
                    + formatted_messages
                )
            else:
                all_messages = formatted_messages

            # Calculate tokens and check limits
            input_tokens = self.count_message_tokens(all_messages)
            if not self.check_token_limit(input_tokens):
                raise TokenLimitExceeded(self.get_limit_error_message(input_tokens))

            # Enforce context window budget
            context_window = self.get_context_window()
            completion_budget = self.max_tokens if self.model not in REASONING_MODELS else self.max_tokens
            safety_buffer = 500
            max_prompt_tokens = max(1024, context_window - completion_budget - safety_buffer)
            if input_tokens > max_prompt_tokens:
                logger.warning(
                    f"Input tokens {input_tokens} exceed max prompt budget {max_prompt_tokens} for images. Truncating conversation."
                )
                # all_messages = self._truncate_messages_to_token_limit(all_messages, max_prompt_tokens)
                input_tokens = self.count_message_tokens(all_messages)

            # Set up API parameters
            params = {
                "model": self.model,
                "messages": all_messages,
                "stream": stream,
            }

            # Add model-specific parameters
            if self.model in REASONING_MODELS:
                params["max_completion_tokens"] = self.max_tokens
            else:
                params["max_tokens"] = self.max_tokens
                params["temperature"] = (
                    temperature if temperature is not None else self.temperature
                )

            # 如果是bigmodel，只保留params中的model、messages和关键参数
            if self.api_type == "bigmodel":
                params = {
                    "model": self.model,
                    "messages": all_messages,
                    "max_tokens": self.max_tokens,
                    "temperature": temperature if temperature is not None else self.temperature,
                }

            # Handle non-streaming request
            if not stream:
                response = await self.client.chat.completions.create(**params)

                if not response.choices or not response.choices[0].message.content:
                    raise ValueError("Empty or invalid response from LLM")

                self.update_token_count(response.usage.prompt_tokens, 0, self.model)
                return response.choices[0].message.content

            # Handle streaming request
            self.update_token_count(input_tokens, 0, self.model)
            response = await self.client.chat.completions.create(**params)

            collected_messages = []
            async for chunk in response:
                chunk_message = chunk.choices[0].delta.content or ""
                collected_messages.append(chunk_message)
                print(chunk_message, end="", flush=True)

            print()  # Newline after streaming
            full_response = "".join(collected_messages).strip()

            if not full_response:
                raise ValueError("Empty response from streaming LLM")

            completion_tokens = self.count_tokens(full_response)
            self.update_token_count(0, completion_tokens, self.model)

            return full_response

        except TokenLimitExceeded:
            raise
        except ValueError as ve:
            logger.error(f"Validation error in ask_with_images: {ve}")
            raise
        except OpenAIError as oe:
            logger.error(f"OpenAI API error: {oe}")
            if isinstance(oe, AuthenticationError):
                logger.error("Authentication failed. Check API key.")
            elif isinstance(oe, RateLimitError):
                logger.error("Rate limit exceeded. Consider increasing retry attempts.")
            elif isinstance(oe, APIError):
                logger.error(f"API error: {oe}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error in ask_with_images: {e}")
            raise
    def detect_image_format(self, image_path):
        with Image.open(image_path) as img:
            img_format = img.format.lower() # type: ignore
            if img_format == "jpeg":
                return "image/jpeg"
            elif img_format == "png":
                return "image/png"
            elif img_format == "gif":
                return "image/gif"
            else:
                return "image/png"  # fallback
    @retry(
        wait=wait_random_exponential(min=1, max=60),
        stop=stop_after_attempt(6),
        retry=retry_if_exception(_should_retry_llm_error),
    )
    async def ask_with_video(
        self,
        messages: List[Union[dict, Message]],
        videos: List[Union[str, dict]] = [],
        system_msgs: Optional[List[Union[dict, Message]]] = None,
        stream: bool = False,
        temperature: Optional[float] = None,
        video_paths: Optional[List[str]] = None,
    ) -> str:
        """
        Send a prompt with videos to the LLM and get the response.

        Args:
            messages: List of conversation messages
            videos: List of video URLs or video data dictionaries
            system_msgs: Optional system messages to prepend
            stream (bool): Whether to stream the response
            temperature (float): Sampling temperature for the response
            video_paths: Optional list of local video file paths (for BigModel compatibility)

        Returns:
            str: The generated response

        Raises:
            TokenLimitExceeded: If token limits are exceeded
            ValueError: If messages are invalid or response is empty
            OpenAIError: If API call fails after retries
            Exception: For unexpected errors
        """
        try:
            # For ask_with_video, we always set supports_images to True because
            # this method should only be called with models that support videos
            if self.model not in MULTIMODAL_MODELS:
                raise ValueError(
                    f"Model {self.model} does not support videos. Use a model from {MULTIMODAL_MODELS}"
                )

            # Format messages with image support
            formatted_messages = self.format_messages(messages, supports_images=True)

            # Ensure the last message is from the user to attach videos
            if not formatted_messages or formatted_messages[-1]["role"] != "user":
                raise ValueError(
                    "The last message must be from the user to attach videos"
                )

            # Process the last user message to include videos
            last_message = formatted_messages[-1]

            # Convert content to multimodal format if needed
            content = last_message["content"]
            multimodal_content = (
                [{"type": "text", "text": content}]
                if isinstance(content, str)
                else content
                if isinstance(content, list)
                else []
            )

            # Add videos to content
            for video in videos:
                if isinstance(video, str):
                    multimodal_content.append(
                        {"type": "video_url", "video_url": {"url": video}}
                    )
                elif isinstance(video, dict) and "url" in video:
                    multimodal_content.append({"type": "video_url", "video_url": video})
                elif isinstance(video, dict) and "video_url" in video:
                    multimodal_content.append(video)
                else:
                    raise ValueError(f"Unsupported video format: {video}")
            # Add video_paths if provided (for compatibility
            if video_paths:
                for video_path in video_paths:
                    # For standard APIs, convert local paths to base64 data URIs
                    with open(video_path, "rb") as video_file:
                        base64_video = base64.b64encode(video_file.read()).decode('utf-8')
                    multimodal_content.append(
                            {"type": "video_url", "video_url": {"url": base64_video}} # type: ignore
                        )

            # Update the message with multimodal content
            last_message["content"] = multimodal_content

            # Add system messages if provided
            if system_msgs:
                all_messages = (
                    self.format_messages(system_msgs, supports_images=True)
                    + formatted_messages
                )
            else:
                all_messages = formatted_messages

            # Calculate tokens and check limits
            input_tokens = self.count_message_tokens(all_messages)
            if not self.check_token_limit(input_tokens):
                raise TokenLimitExceeded(self.get_limit_error_message(input_tokens))

            # Enforce context window budget
            context_window = self.get_context_window()
            completion_budget = self.max_tokens if self.model not in REASONING_MODELS else self.max_tokens
            safety_buffer = 500
            max_prompt_tokens = max(1024, context_window - completion_budget - safety_buffer)
            if input_tokens > max_prompt_tokens:
                logger.warning(
                    f"Input tokens {input_tokens} exceed max prompt budget {max_prompt_tokens} for videos. Truncating conversation."
                )
                # all_messages = self._truncate_messages_to_token_limit(all_messages, max_prompt_tokens)
                input_tokens = self.count_message_tokens(all_messages)

            # Set up API parameters
            params = {
                "model": self.model,
                "messages": all_messages,
                "stream": stream,
            }

            # Add model-specific parameters
            if self.model in REASONING_MODELS:
                params["max_completion_tokens"] = self.max_tokens
            else:
                params["max_tokens"] = self.max_tokens
                params["temperature"] = (
                    temperature if temperature is not None else self.temperature
                )
            if self.api_type == "bigmodel":
                params = {
                    "model": "GLM-4.1V-Thinking-Flash",
                    "messages": all_messages,
                    "max_tokens": self.max_tokens,
                    "temperature": temperature if temperature is not None else self.temperature,
                }
            # Handle non-streaming request
            if not stream:
                response = await self.client.chat.completions.create(**params)

                if not response.choices or not response.choices[0].message.content:
                    raise ValueError("Empty or invalid response from LLM")

                self.update_token_count(response.usage.prompt_tokens, 0, self.model)
                return response.choices[0].message.content

            # Handle streaming request
            self.update_token_count(input_tokens, 0, self.model)
            response = await self.client.chat.completions.create(**params)

            collected_messages = []
            async for chunk in response:
                chunk_message = chunk.choices[0].delta.content or ""
                collected_messages.append(chunk_message)
                print(chunk_message, end="", flush=True)

            print()  # Newline after streaming
            full_response = "".join(collected_messages).strip()

            if not full_response:
                raise ValueError("Empty response from streaming LLM")

            completion_tokens = self.count_tokens(full_response)
            self.update_token_count(0, completion_tokens, self.model)

            return full_response

        except TokenLimitExceeded:
            raise
        except ValueError as ve:
            logger.error(f"Validation error in ask_with_video: {ve}")
            raise
        except OpenAIError as oe:
            logger.error(f"OpenAI API error: {oe}")
            if isinstance(oe, AuthenticationError):
                logger.error("Authentication failed. Check API key.")
            elif isinstance(oe, RateLimitError):
                logger.error("Rate limit exceeded. Consider increasing retry attempts.")
            elif isinstance(oe, APIError):
                logger.error(f"API error: {oe}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error in ask_with_video: {e}")
            raise

    @retry(
        wait=wait_random_exponential(min=1, max=60),
        stop=stop_after_attempt(6),
        retry=retry_if_exception(_should_retry_llm_error),
    )
    async def ask_tool(
        self,
        messages: List[Union[dict, Message]],
        system_msgs: Optional[List[Union[dict, Message]]] = None,
        timeout: int = 30,
        tools: Optional[List[dict]] = None,
        tool_choice: TOOL_CHOICE_TYPE = ToolChoice.AUTO,  # type: ignore
        temperature: Optional[float] = None,
        new_model: Optional[str] = None,
        **kwargs,
    ) -> ChatCompletionMessage | None:
        """
        Ask LLM using functions/tools and return the response.

        Args:
            messages: List of conversation messages
            system_msgs: Optional system messages to prepend
            timeout: Request timeout in seconds
            tools: List of tools to use
            tool_choice: Tool choice strategy
            temperature: Sampling temperature for the response
            **kwargs: Additional completion arguments

        Returns:
            ChatCompletionMessage: The model's response

        Raises:
            TokenLimitExceeded: If token limits are exceeded
            ValueError: If tools, tool_choice, or messages are invalid
            OpenAIError: If API call fails after retries
            Exception: For unexpected errors
        """
        try:
            # Validate tool_choice
            if tool_choice not in TOOL_CHOICE_VALUES:
                raise ValueError(f"Invalid tool_choice: {tool_choice}")

            model_in_use = new_model if new_model else self.model
            # Use the provided model or default to self.model

            # Check if the model supports images
            supports_images = model_in_use in MULTIMODAL_MODELS

            import os
            # Format messages
            if system_msgs:
                system_msgs = self.format_messages(system_msgs, supports_images)
                messages = system_msgs + self.format_messages(messages, supports_images)
            else:
                messages = self.format_messages(messages, supports_images)

            # --- Prompt Caching: mark system message for Anthropic models ---
            # For claude-* models via Anthropic's API, wrap the system message content
            # in a block array with cache_control so the static prompt is cached
            # across calls within the same 5-minute window (saves ~40-50% prompt tokens).
            if model_in_use.startswith("claude-"):
                new_messages = []
                for msg in messages:
                    if msg.get("role") == "system":
                        content = msg.get("content", "")
                        if isinstance(content, str) and content:
                            msg = dict(msg)
                            msg["content"] = [
                                {"type": "text", "text": content, "cache_control": {"type": "ephemeral"}}
                            ]
                    new_messages.append(msg)
                messages = new_messages
                # Claude does not support "assistant message prefill" — the last message
                # must be a user turn.  When the agent's content-only response was the
                # last thing added to memory, the next think() call violates this.
                # Inject a synthetic user continuation so the API call succeeds.
                if messages and messages[-1].get("role") == "assistant":
                    messages = messages + [{"role": "user", "content": "Please continue."}]

            # --- History Trimming: truncate old tool results to reduce context bloat ---
            # Keep the last HISTORY_KEEP_FULL tool messages at full length;
            # truncate older tool/function messages to 300 chars to save tokens.
            HISTORY_KEEP_FULL = 4  # keep last 4 tool messages untruncated
            HISTORY_MAX_OLD = 300
            tool_msg_indices = [
                i for i, m in enumerate(messages) if m.get("role") in ("tool", "function")
            ]
            trim_indices = set(tool_msg_indices[:-HISTORY_KEEP_FULL]) if len(tool_msg_indices) > HISTORY_KEEP_FULL else set()
            if trim_indices:
                trimmed = []
                for i, msg in enumerate(messages):
                    if i in trim_indices:
                        content = msg.get("content", "")
                        if isinstance(content, str) and len(content) > HISTORY_MAX_OLD:
                            msg = dict(msg)
                            msg["content"] = content[:HISTORY_MAX_OLD] + f"...[trimmed, orig {len(content)} chars]"
                    trimmed.append(msg)
                messages = trimmed

            # Calculate input token count
            input_tokens = self.count_message_tokens(messages)

            from app.utils.debug_logger import debug_print
            # debug_print(f"ask tool message {messages}\n")
            # If there are tools, calculate token count for tool descriptions
            tools_tokens = 0
            if tools:
                for tool in tools:
                    tools_tokens += self.count_tokens(str(tool))

            input_tokens += tools_tokens

            # Check if token limits are exceeded
            if not self.check_token_limit(input_tokens):
                error_message = self.get_limit_error_message(input_tokens)
                # Raise a special exception that won't be retried
                raise TokenLimitExceeded(error_message)

            # Enforce context window budget before sending
            context_window = self.get_context_window()
            completion_budget = self.max_tokens if model_in_use not in REASONING_MODELS else self.max_tokens
            safety_buffer = 500
            max_prompt_tokens = max(1024, context_window - completion_budget - safety_buffer)
            if input_tokens > max_prompt_tokens:
                logger.warning(
                    f"Input tokens {input_tokens} (incl. tools) exceed max prompt budget {max_prompt_tokens}. Truncating conversation."
                )
                # Only truncate the messages; we assume tools are required
                # messages = self._truncate_messages_to_token_limit(messages, max(0, max_prompt_tokens - tools_tokens))
                input_tokens = self.count_message_tokens(messages) + tools_tokens

            # Validate tools if provided
            if tools:
                for tool in tools:
                    if not isinstance(tool, dict) or "type" not in tool:
                        raise ValueError("Each tool must be a dict with 'type' field")

            # logger.info(f"Tool choice: {tool_choice}")
            # logger.info(f"Tools: {tools}")
            # logger.debug(f"Messages: {messages}")
            from app.utils.debug_logger import llm_trace, truncate_msg_content

            # Set up the completion request
            params = {
                "model": model_in_use,
                "messages": messages,
                "tools": tools,
                "tool_choice": tool_choice,
                "timeout": timeout,
                **kwargs,
            }

            if model_in_use in REASONING_MODELS:
                params["max_completion_tokens"] = self.max_tokens
            else:
                params["max_tokens"] = self.max_tokens
                params["temperature"] = (
                    temperature if temperature is not None else self.temperature
                )

            params["stream"] = False  # Always use non-streaming for tool requests

            # ── LLM Response Cache ───────────────────────────────────────────
            from app.llm_cache import (
                get_cached_ask_tool, store_cached_ask_tool,
                is_cache_enabled, is_cache_bypass,
            )
            _cache_active_tool = is_cache_enabled() and not is_cache_bypass()
            if _cache_active_tool:
                _cached_tool = get_cached_ask_tool(model_in_use, messages, tools)
                if _cached_tool is not None:
                    return _cached_tool
            # ────────────────────────────────────────────────────────────────

            # Log last 6 messages (truncated) + tools summary
            tail_msgs = messages[-6:] if len(messages) > 6 else messages
            llm_trace(
                f"{'='*80}\n[ASK_TOOL INPUT] model={model_in_use} total_msgs={len(messages)} (showing last {len(tail_msgs)})\n"
                + "\n".join(
                    f"[{i}] role={m.get('role','?')} : {json.dumps(truncate_msg_content(m), ensure_ascii=False)}"
                    for i, m in enumerate(tail_msgs)
                )
                + f"\n--- Tools ({len(tools) if tools else 0}) ---\n"
                + (", ".join(t.get("function", {}).get("name", "?") for t in tools) if tools else "none")
                + f"\n{'='*80}"
            )
            response: ChatCompletion = await self.client.chat.completions.create(
                **params
            )

            llm_trace(f"[ASK_TOOL OUTPUT] model={model_in_use}\n{response.choices[0].message}\n{'='*80}")

            # Check if response is valid
            if not response.choices or not response.choices[0].message:
                print(response)
                # raise ValueError("Invalid or empty response from LLM")
                return None

            logger.debug(f"Tool response: {response.choices[0].message}")

            # Update token counts
            self.update_token_count(
                response.usage.prompt_tokens, response.usage.completion_tokens, model_in_use
            )
            llm_trace(
                f"[TOKENS] model={model_in_use} "
                f"prompt={response.usage.prompt_tokens} completion={response.usage.completion_tokens} "
                f"total={response.usage.total_tokens}"
            )

            logger.debug(
                f"Tool response: {response.choices[0].message}"
            )

            _tool_result = response.choices[0].message
            if _cache_active_tool and _tool_result is not None:
                store_cached_ask_tool(model_in_use, messages, tools, _tool_result)
            return _tool_result

        except TokenLimitExceeded:
            # Re-raise token limit errors without logging
            raise
        except ValueError as ve:
            logger.error(f"Validation error in ask_tool: {ve}")
            raise
        except OpenAIError as oe:
            logger.error(f"Error found for llm {self.model} in ask_tool")
            logger.error(f"OpenAI API error: {oe}")
            from app.utils.debug_logger import llm_trace, truncate_msg_content
            llm_trace(f"[ASK_TOOL ERROR] {oe}\n" + "\n".join(
                f"  msg[{i}] role={msg.get('role','?')}: {json.dumps(truncate_msg_content(msg), ensure_ascii=False)}"
                for i, msg in enumerate(messages)
            ))
            if isinstance(oe, AuthenticationError):
                logger.error("Authentication failed. Check API key.")
            elif isinstance(oe, RateLimitError):
                logger.error("Rate limit exceeded. Consider increasing retry attempts.")
            elif isinstance(oe, APIError):
                logger.error(f"API error: {oe}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error in ask_tool: {e}")
            raise


if __name__ == "__main__":
    import asyncio
    async def main():
        llm = LLM(config_name="bigmodel")
        # 测试图片解析
        result = await llm.ask_with_images(
            messages=[
                {"role": "user", "content": "请描述这张图片"}
            ],
            image_paths=[r"D:\aaa_code\python\AI助教\edumanus2\assets\9ac305254b167fcaab9d87b6382e53c.png"]
        )
        print(result)
    # 测试视频
    async def test_video():
        llm = LLM(config_name="free_vision_reasoning")
        result = await llm.ask_with_video(
            messages=[{"role": "user", "content": "请描述这个视频"}],
            video_paths=[
                r"D:\aaa_code\python\AI助教\edumanus2\assets\Video_Ready_Glowing_Glass_Ball.mp4"
            ],
        )# type: ignore
        print(result)

    asyncio.run(test_video())
