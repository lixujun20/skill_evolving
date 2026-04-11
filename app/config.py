import json
import threading
import tomllib
from pathlib import Path
from typing import Dict, List, Optional

from pydantic import BaseModel, Field, validator


def get_project_root() -> Path:
    """Get the project root directory"""
    return Path(__file__).resolve().parent.parent


PROJECT_ROOT = get_project_root()
WORKSPACE_ROOT = PROJECT_ROOT / "workspace"


class LLMSettings(BaseModel):
    model: str = Field(..., description="Model name")
    base_url: str = Field(..., description="API base URL")
    api_key: str = Field(..., description="API key")
    max_tokens: int = Field(4096, description="Maximum number of tokens per request")
    max_input_tokens: Optional[int] = Field(
        None,
        description="Maximum input tokens to use across all requests (None for unlimited)",
    )
    temperature: float = Field(1.0, description="Sampling temperature")
    api_type: str = Field(..., description="Azure, Openai, or Ollama")
    api_version: str = Field(..., description="Azure Openai version if AzureOpenai")


class ProxySettings(BaseModel):
    server: str = Field(None, description="Proxy server address")
    username: Optional[str] = Field(None, description="Proxy username")
    password: Optional[str] = Field(None, description="Proxy password")


class SearchSettings(BaseModel):
    engine: str = Field(default="Google", description="Search engine the llm to use")
    fallback_engines: List[str] = Field(
        default_factory=lambda: ["DuckDuckGo", "Baidu", "Bing"],
        description="Fallback search engines to try if the primary engine fails",
    )
    retry_delay: int = Field(
        default=60,
        description="Seconds to wait before retrying all engines again after they all fail",
    )
    max_retries: int = Field(
        default=3,
        description="Maximum number of times to retry all engines when all fail",
    )
    lang: str = Field(
        default="en",
        description="Language code for search results (e.g., en, zh, fr)",
    )
    country: str = Field(
        default="us",
        description="Country code for search results (e.g., us, cn, uk)",
    )

class BraveSearchSettings(BaseModel):
    """Configuration for Brave Search"""
    api_key: str = Field(default="", description="Brave Search API key")
    base_url: str = Field(
        "https://api.search.brave.com/res/v1", description="Brave Search API base URL"
    )
    max_results: int = Field(
        10, description="Maximum number of search results to return"
    )

class GoogleSearchSettings(BaseModel):
    """Configuration for Google Custom Search"""
    api_key: str = Field(default="", description="Google Custom Search API key")
    cse_id: str = Field(default="", description="Google Custom Search Engine ID")
    max_results: int = Field(
        10, description="Maximum number of search results to return"
    )

class ZhipuSearchSettings(BaseModel):
    """Configuration for Zhipu Search"""
    api_key: str = Field(default="", description="Zhipu Search API key")
    base_url: str = Field(
        default="https://open.bigmodel.cn/api/paas/v4",
        description="Zhipu Search API base URL",
    )

class BochaSearchSettings(BaseModel):
    """Configuration for Bocha Search"""
    api_key: str = Field(default="", description="Bocha Search API key")
    base_url: str = Field(
        default="https://api.bochaai.com/v1/web-search",
        description="Bocha Search API base URL",
    )

class IFlytekSpeechSettings(BaseModel):
    """Configuration for iFlytek Speech Recognition"""
    app_id: str = Field(default="", description="iFlytek App ID")
    api_key: str = Field(default="", description="iFlytek API Key")
    api_secret: str = Field(default="", description="iFlytek API Secret (optional for real-time transcription)")

class PPTAPISettings(BaseModel):
    """Configuration for PPT Generation APIs"""
    glm_api_key: str = Field(default="", description="Zhipu GLM API Key for PPT generation")
    gemini_api_key: str = Field(default="", description="Google Gemini API Key for PPT generation")

class GoogleTTSSettings(BaseModel):
    """Configuration for Google Cloud Text-to-Speech"""
    credentials_path: Optional[str] = Field(
        default=None,
        description="Path to Google Cloud service account JSON credentials file"
    )
    project_id: Optional[str] = Field(
        default=None,
        description="Google Cloud project ID"
    )
    voice_name: str = Field(
        default="cmn-CN-Chirp3-HD-Leda",
        description="Google TTS voice name (Chirp 3 HD voices)"
    )
    language_code: str = Field(
        default="cmn-CN",
        description="Language code for TTS synthesis"
    )
    speaking_rate: float = Field(
        default=1.0,
        description="Speaking rate/speed (0.25 to 4.0)"
    )
    pitch: float = Field(
        default=0.0,
        description="Speaking pitch (-20.0 to 20.0)"
    )
    audio_encoding: str = Field(
        default="MP3",
        description="Audio encoding format (MP3, LINEAR16, OGG_OPUS)"
    )

    @validator('speaking_rate')
    def validate_speaking_rate(cls, v):
        """Validate speaking rate is within allowed range"""
        if not 0.25 <= v <= 4.0:
            raise ValueError('speaking_rate must be between 0.25 and 4.0')
        return v

    @validator('pitch')
    def validate_pitch(cls, v):
        """Validate pitch is within allowed range"""
        if not -20.0 <= v <= 20.0:
            raise ValueError('pitch must be between -20.0 and 20.0')
        return v

class BrowserSettings(BaseModel):
    headless: bool = Field(False, description="Whether to run browser in headless mode")
    disable_security: bool = Field(
        True, description="Disable browser security features"
    )
    extra_chromium_args: List[str] = Field(
        default_factory=list, description="Extra arguments to pass to the browser"
    )
    chrome_instance_path: Optional[str] = Field(
        None, description="Path to a Chrome instance to use"
    )
    wss_url: Optional[str] = Field(
        None, description="Connect to a browser instance via WebSocket"
    )
    cdp_url: Optional[str] = Field(
        None, description="Connect to a browser instance via CDP"
    )
    proxy: Optional[ProxySettings] = Field(
        None, description="Proxy settings for the browser"
    )
    max_content_length: int = Field(
        2000, description="Maximum length for content retrieval operations"
    )


class SandboxSettings(BaseModel):
    """Configuration for the execution sandbox"""

    use_sandbox: bool = Field(False, description="Whether to use the sandbox")
    image: str = Field("python:3.12-slim", description="Base image")
    work_dir: str = Field("/workspace", description="Container working directory")
    memory_limit: str = Field("512m", description="Memory limit")
    cpu_limit: float = Field(1.0, description="CPU limit")
    timeout: int = Field(300, description="Default command timeout (seconds)")
    network_enabled: bool = Field(
        False, description="Whether network access is allowed"
    )


class MCPServerConfig(BaseModel):
    """Configuration for a single MCP server"""

    type: str = Field(..., description="Server connection type (sse or stdio)")
    url: Optional[str] = Field(None, description="Server URL for SSE connections")
    command: Optional[str] = Field(None, description="Command for stdio connections")
    args: List[str] = Field(
        default_factory=list, description="Arguments for stdio command"
    )


class MatlabWebSocketSettings(BaseModel):
    """Configuration for MATLAB WebSocket Workflow Tool"""

    websocket_url: str = Field(
        default="ws://localhost:9001",
        description="WebSocket server URL for MATLAB connection"
    )
    connection_timeout: int = Field(
        default=30,
        description="Connection timeout in seconds"
    )
    response_timeout: int = Field(
        default=60,
        description="Response timeout in seconds"
    )
    ping_timeout: int = Field(
        default=10,
        description="Ping timeout in seconds"
    )
    max_retries: int = Field(
        default=3,
        description="Maximum number of retry attempts"
    )
    retry_delay: int = Field(
        default=5,
        description="Delay between retries in seconds"
    )
    default_session_id: str = Field(
        default="default",
        description="Default session identifier"
    )
    session_cleanup_interval: int = Field(
        default=3600,
        description="Session cleanup interval in seconds"
    )
    use_llm_parsing: bool = Field(
        default=True,
        description="Enable LLM parsing for natural language requests"
    )
    llm_fallback_enabled: bool = Field(
        default=True,
        description="Enable fallback to rule-based parsing when LLM fails"
    )
    max_llm_retries: int = Field(
        default=2,
        description="Maximum LLM parsing retries"
    )
    enable_debug_logging: bool = Field(
        default=False,
        description="Enable debug logging for WebSocket messages"
    )
    log_websocket_messages: bool = Field(
        default=False,
        description="Log all WebSocket message content"
    )
    validate_matlab_commands: bool = Field(
        default=True,
        description="Validate MATLAB commands before execution"
    )
    max_command_length: int = Field(
        default=10000,
        description="Maximum allowed MATLAB command length"
    )
    allowed_matlab_functions: List[str] = Field(
        default_factory=lambda: [
            "plot", "figure", "hold", "grid", "legend", "title", "xlabel", "ylabel",
            "resistor", "capacitor", "inductor", "wire", "ground", "voltage_source", "current_source"
        ],
        description="List of allowed MATLAB functions"
    )

    @validator('websocket_url')
    def validate_websocket_url(cls, v):
        """Validate WebSocket URL format"""
        if not v.startswith(('ws://', 'wss://')):
            raise ValueError('WebSocket URL must start with ws:// or wss://')
        return v

    @validator('connection_timeout', 'response_timeout', 'ping_timeout')
    def validate_positive_timeout(cls, v):
        """Validate timeout values are positive"""
        if v <= 0:
            raise ValueError('Timeout values must be positive')
        return v

    @validator('max_retries')
    def validate_max_retries(cls, v):
        """Validate max_retries is non-negative"""
        if v < 0:
            raise ValueError('max_retries must be non-negative')
        return v


class XuetangSSOSettings(BaseModel):
    """Configuration for Xuetang Online SSO (Single Sign-On)"""

    aes_key: str = Field(
        default="",
        description="AES encryption key for SSO (16/24/32 bytes, provided by Xuetang)"
    )
    secret_key: str = Field(
        default="",
        description="Secret key for signature verification (provided by Xuetang)"
    )
    time_verify: int = Field(
        default=300,
        description="Timestamp verification validity period in seconds"
    )


class MCPSettings(BaseModel):
    """Configuration for MCP (Model Context Protocol)"""

    server_reference: str = Field(
        "app.mcp.server", description="Module reference for the MCP server"
    )
    servers: Dict[str, MCPServerConfig] = Field(
        default_factory=dict, description="MCP server configurations"
    )

    @classmethod
    def load_server_config(cls) -> Dict[str, MCPServerConfig]:
        """Load MCP server configuration from JSON file"""
        config_path = PROJECT_ROOT / "config" / "mcp.json"

        try:
            config_file = config_path if config_path.exists() else None
            if not config_file:
                return {}

            with config_file.open() as f:
                data = json.load(f)
                servers = {}

                for server_id, server_config in data.get("mcpServers", {}).items():
                    servers[server_id] = MCPServerConfig(
                        type=server_config["type"],
                        url=server_config.get("url"),
                        command=server_config.get("command"),
                        args=server_config.get("args", []),
                    )
                return servers
        except Exception as e:
            raise ValueError(f"Failed to load MCP server config: {e}")


class LibrarySettings(BaseModel):
    """Configuration for the library"""

    base_path: str = Field("", description="Base path for the libraries")
    container_base_path: str = Field("", description="Container base path for the libraries")
    test_path: str = Field("", description="Path for the test library")
    tool_code_path: str = Field("", description="Path for the tool code library")
    tool_library_path: str = Field("", description="Path for the tool library")
    workflow_code_path: str = Field("", description="Path for the workflow code library")
    workflow_library_path: str = Field("", description="Path for the workflow library")
    tool_venv_bin_path: str = Field("", description="Path to the python executable in the tool venv")
    file_system_root: str = Field("", description="Root path for the file system within which the agents operate")
    tool_tester_image: str = Field("", description="Docker image name for the tool tester")
    tool_api_port: str = Field("", description="Port for the tool API")

class DifySettings(BaseModel):
    """Configuration for Dify"""

    backend_server_ip: str = Field("", description="Dify Backend server IP")
    backend_server_port: str = Field("", description="Dify Backend server port")
    ws_server_ip: str = Field("", description="Dify WebSocket server IP")
    ws_server_port: str = Field("", description="Dify WebSocket server port")
    bridge_api_ip: str = Field("", description="Dify Bridge API IP")
    bridge_api_port: str = Field("", description="Dify Bridge API port")
    tool_server_port: str = Field("", description="Dify Tool server port")
    email: str = Field("", description="email for Dify local account")
    password_env: str = Field("", description="password environment variable for Dify local account")
    restore_user_allocations: bool = Field(False, description="Whether to restore user ID allocations on startup")
    jwt_secret_key: str = Field("", description="JWT secret key for k8s authentication")

class AppConfig(BaseModel):
    llm: Dict[str, LLMSettings]
    sandbox: Optional[SandboxSettings] = Field(
        None, description="Sandbox configuration"
    )
    browser_config: Optional[BrowserSettings] = Field(
        None, description="Browser configuration"
    )
    search_config: Optional[SearchSettings] = Field(
        None, description="Search configuration"
    )
    mcp_config: Optional[MCPSettings] = Field(None, description="MCP configuration")
    brave_search_config: Optional[BraveSearchSettings] = Field(None, description="Brave Search configuration")
    google_search_config: Optional[GoogleSearchSettings] = Field(None, description="Google Custom Search configuration")
    zhipu_search_config: Optional[ZhipuSearchSettings] = Field(None, description="Zhipu Search configuration")
    bocha_search_config: Optional[BochaSearchSettings] = Field(None, description="Bocha Search configuration")
    iflytek_speech_config: Optional[IFlytekSpeechSettings] = Field(None, description="iFlytek Speech Recognition configuration")
    ppt_api_config: Optional[PPTAPISettings] = Field(None, description="PPT Generation API configuration")
    google_tts_config: Optional[GoogleTTSSettings] = Field(None, description="Google Cloud Text-to-Speech configuration")
    matlab_websocket_config: Optional[MatlabWebSocketSettings] = Field(None, description="MATLAB WebSocket configuration")
    xuetang_sso_config: Optional[XuetangSSOSettings] = Field(None, description="Xuetang Online SSO configuration")
    jwt_secret_key: Optional[str] = Field(None, description="JWT Secret Key")
    library_config: Optional[LibrarySettings] = Field(None, description="Library configuration")
    dify_config: Optional[DifySettings] = Field(None, description="Dify configuration")

    class Config:
        arbitrary_types_allowed = True


class Config:
    _instance = None
    _lock = threading.Lock()
    _initialized = False

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if not self._initialized:
            with self._lock:
                if not self._initialized:
                    self._config = None
                    self._load_initial_config()
                    self._initialized = True

    @staticmethod
    def _get_config_path() -> Path:
        root = PROJECT_ROOT
        config_path = root / "config" / "config.toml"
        if config_path.exists():
            return config_path
        yaml_path = root / "config" / "config.yaml"
        if yaml_path.exists():
            return yaml_path
        
        example_path = root / "config" / "config.example.toml"
        if example_path.exists():
            return example_path
        example_yaml_path = root / "config" / "config.example.yaml"
        if example_yaml_path.exists():
            return example_yaml_path

        raise FileNotFoundError("No configuration file found in config directory")

    def _load_config(self) -> dict:
        config_path = self._get_config_path()
        if config_path.suffix in ['.yaml', '.yml']:
            import yaml
            with config_path.open("r", encoding='utf-8') as f:
                return yaml.safe_load(f)
        else:
            with config_path.open("rb") as f:
                return tomllib.load(f)

    def _load_initial_config(self):
        raw_config = self._load_config()
        # from app.logger import logger
        # logger.info(f"Raw configuration loaded: {raw_config}")
        base_llm = raw_config.get("llm", {})
        llm_overrides = {
            k: v for k, v in raw_config.get("llm", {}).items() if isinstance(v, dict)
        }

        default_settings = {
            "model": base_llm.get("model"),
            "base_url": base_llm.get("base_url"),
            "api_key": base_llm.get("api_key"),
            "max_tokens": base_llm.get("max_tokens", 4096),
            "max_input_tokens": base_llm.get("max_input_tokens"),
            "temperature": base_llm.get("temperature", 1.0),
            "api_type": base_llm.get("api_type", ""),
            "api_version": base_llm.get("api_version", ""),
        }

        # handle browser config.
        browser_config = raw_config.get("browser", {})
        brave_search_config = raw_config.get("brave_search", {})
        google_search_config = raw_config.get("google_search", {})
        zhipu_search_config = raw_config.get("zhipu_search", {})
        bocha_search_config = raw_config.get("bocha_search", {})
        browser_settings = None

        if browser_config:
            # handle proxy settings.
            proxy_config = browser_config.get("proxy", {})
            proxy_settings = None

            if proxy_config and proxy_config.get("server"):
                proxy_settings = ProxySettings(
                    **{
                        k: v
                        for k, v in proxy_config.items()
                        if k in ["server", "username", "password"] and v
                    }
                )

            # filter valid browser config parameters.
            valid_browser_params = {
                k: v
                for k, v in browser_config.items()
                if k in BrowserSettings.__annotations__ and v is not None
            }

            # if there is proxy settings, add it to the parameters.
            if proxy_settings:
                valid_browser_params["proxy"] = proxy_settings

            # only create BrowserSettings when there are valid parameters.
            if valid_browser_params:
                browser_settings = BrowserSettings(**valid_browser_params)

        search_config = raw_config.get("search", {})
        search_settings = None
        if search_config:
            search_settings = SearchSettings(**search_config)
        sandbox_config = raw_config.get("sandbox", {})
        if sandbox_config:
            sandbox_settings = SandboxSettings(**sandbox_config)
        else:
            sandbox_settings = SandboxSettings()

        mcp_config = raw_config.get("mcp", {})
        mcp_settings = None
        if mcp_config:
            # Load server configurations from JSON
            mcp_config["servers"] = MCPSettings.load_server_config()
            mcp_settings = MCPSettings(**mcp_config)
        else:
            mcp_settings = MCPSettings(servers=MCPSettings.load_server_config())

        brave_search_settings = None
        if brave_search_config:
            brave_search_settings = BraveSearchSettings(**brave_search_config)
        else:
            brave_search_settings = BraveSearchSettings()

        google_search_settings = None
        if google_search_config:
            google_search_settings = GoogleSearchSettings(**google_search_config)
        else:
            google_search_settings = GoogleSearchSettings()

        zhipu_search_settings = None
        if zhipu_search_config:
            zhipu_search_settings = ZhipuSearchSettings(**zhipu_search_config)
        else:
            zhipu_search_settings = ZhipuSearchSettings()

        bocha_search_settings = None
        if bocha_search_config:
            bocha_search_settings = BochaSearchSettings(**bocha_search_config)
        else:
            bocha_search_settings = BochaSearchSettings()

        # Handle iFlytek Speech configuration
        iflytek_speech_config = raw_config.get("iflytek_speech", {})
        iflytek_speech_settings = None
        if iflytek_speech_config:
            iflytek_speech_settings = IFlytekSpeechSettings(**iflytek_speech_config)
        else:
            iflytek_speech_settings = IFlytekSpeechSettings()

        # Handle PPT API configuration
        ppt_api_config = raw_config.get("ppt_api", {})
        ppt_api_settings = None
        if ppt_api_config:
            ppt_api_settings = PPTAPISettings(**ppt_api_config)
        else:
            ppt_api_settings = PPTAPISettings()

        # Handle Google TTS configuration
        google_tts_config = raw_config.get("google_tts", {})
        google_tts_settings = None
        if google_tts_config:
            google_tts_settings = GoogleTTSSettings(**google_tts_config)
        else:
            google_tts_settings = GoogleTTSSettings()

        # Handle MATLAB WebSocket configuration
        matlab_websocket_config = raw_config.get("matlab_websocket", {})
        matlab_websocket_settings = None
        if matlab_websocket_config:
            matlab_websocket_settings = MatlabWebSocketSettings(**matlab_websocket_config)
        else:
            matlab_websocket_settings = MatlabWebSocketSettings()

        library_config = raw_config.get("library", {})
        library_settings = None
        if library_config:
            library_settings = LibrarySettings(**library_config)
        else:
            library_settings = LibrarySettings()

        dify_config = raw_config.get('dify', {})
        dify_settings = None
        if dify_config:
            dify_settings = DifySettings(**dify_config)
        else:
            dify_settings = DifySettings()
        # Handle Xuetang SSO configuration
        xuetang_sso_config = raw_config.get("xuetang_sso", {})
        xuetang_sso_settings = None
        if xuetang_sso_config:
            xuetang_sso_settings = XuetangSSOSettings(**xuetang_sso_config)
        else:
            xuetang_sso_settings = XuetangSSOSettings()

        config_dict = {
            "llm": {
                "default": default_settings,
                **{
                    name: {**default_settings, **override_config}
                    for name, override_config in llm_overrides.items()
                },
            },
            "sandbox": sandbox_settings,
            "browser_config": browser_settings,
            "search_config": search_settings,
            "mcp_config": mcp_settings,
            "brave_search_config": brave_search_settings,
            "google_search_config": google_search_settings,
            "zhipu_search_config": zhipu_search_settings,
            "bocha_search_config": bocha_search_settings,
            "iflytek_speech_config": iflytek_speech_settings,
            "ppt_api_config": ppt_api_settings,
            "google_tts_config": google_tts_settings,
            "matlab_websocket_config": matlab_websocket_settings,
            "jwt_secret_key": (raw_config.get("jwt_secret_key") or {}).get("jwt_secret_key"),
            "library_config": library_settings,
            "dify_config": dify_settings,
            "xuetang_sso_config": xuetang_sso_settings,
        }

        self._config = AppConfig(**config_dict)

    @property
    def llm(self) -> Dict[str, LLMSettings]:
        return self._config.llm

    @property
    def sandbox(self) -> SandboxSettings:
        return self._config.sandbox

    @property
    def browser_config(self) -> Optional[BrowserSettings]:
        return self._config.browser_config

    @property
    def search_config(self) -> Optional[SearchSettings]:
        return self._config.search_config

    @property
    def mcp_config(self) -> MCPSettings:
        """Get the MCP configuration"""
        return self._config.mcp_config
    
    @property
    def library_config(self) -> LibrarySettings:
        """Get the library configuration"""
        return self._config.library_config
    
    @property
    def dify(self) -> DifySettings:
        """Get the dify configuration"""
        return self._config.dify_config

    @property
    def workspace_root(self) -> Path:
        """Get the workspace root directory"""
        return WORKSPACE_ROOT

    @property
    def root_path(self) -> Path:
        """Get the root path of the application"""
        return PROJECT_ROOT

    @property
    def brave_search(self) -> BraveSearchSettings:
        """Get the Brave Search configuration"""
        return self._config.brave_search_config

    @property
    def google_search(self) -> GoogleSearchSettings:
        """Get the Google Custom Search configuration"""
        return self._config.google_search_config

    @property
    def zhipu_search(self) -> ZhipuSearchSettings:
        """Get the Zhipu Search configuration"""
        return self._config.zhipu_search_config

    @property
    def bocha_search(self) -> BochaSearchSettings:
        """Get the Bocha Search configuration"""
        return self._config.bocha_search_config

    @property
    def iflytek_speech(self) -> IFlytekSpeechSettings:
        """Get the iFlytek Speech Recognition configuration"""
        return self._config.iflytek_speech_config

    @property
    def ppt_api(self) -> PPTAPISettings:
        """Get the PPT Generation API configuration"""
        return self._config.ppt_api_config

    @property
    def google_tts(self) -> GoogleTTSSettings:
        """Get the Google Cloud Text-to-Speech configuration"""
        return self._config.google_tts_config

    @property
    def matlab_websocket(self) -> MatlabWebSocketSettings:
        """Get the MATLAB WebSocket configuration"""
        return self._config.matlab_websocket_config

    @property
    def xuetang_sso(self) -> XuetangSSOSettings:
        """Get the Xuetang Online SSO configuration"""
        return self._config.xuetang_sso_config

    @property
    def jwt_secret_key(self) -> str:
        """Get the JWT secret key"""
        if not self._config.jwt_secret_key:
            raise ValueError("jwt_secret_key is not set in the configuration file.")
        return self._config.jwt_secret_key


config = Config()
