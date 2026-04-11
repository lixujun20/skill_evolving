# Skill Review Guidelines

This document defines the review standards for Python Skills extracted by AI Agents. All generated Skills must undergo strict review by a "Test Engineer (Reviewer Agent)" to ensure they meet **Software Engineering Efficiency** (eliminating common anti-patterns) and **Functional Usability** (high-coverage testing standards).

---

## 1. Software Engineering Efficiency: Anti-Patterns Review

The Reviewer must check the code for the presence of the following 8 Anti-Patterns. Any matched code must be rejected or requested for refactoring.

### 1.1 Duplication
- **Definition**: Manually writing low-level logic for extremely common generic operations (e.g., date parsing, sending requests) instead of using standard libraries or mature third-party libraries.
- **Negative Example**:
  ```python
  def parse_date(date_str):
      # Manually splitting and parsing
      parts = date_str.split("-")
      return int(parts[0]), int(parts[1]), int(parts[2])
  ```
- **Positive Example**:
  ```python
  from datetime import datetime
  def parse_date(date_str: str) -> datetime:
      return datetime.strptime(date_str, "%Y-%m-%d")
  ```
- **Guideline**: Prioritize built-in or mainstream libraries like `datetime`, `requests`, `pandas`.

### 1.2 Hardcoding
- **Definition**: Hardcoding volatile configurations (URLs, file paths, specific target objects, specific Tokens/Keys) inside functions, making the function non-reusable.
- **Negative Example**:
  ```python
  def get_aapl_stock():
      resp = requests.get("https://api.finance.com/v1/stock/AAPL")
      return resp.json()
  ```
- **Positive Example**:
  ```python
  def get_stock_data(symbol: str, base_url: str = "https://api.finance.com/v1/stock") -> dict:
      resp = requests.get(f"{base_url}/{symbol.upper()}")
      return resp.json()
  ```
- **Guideline**: All non-deterministic constants must be elevated to function default parameters.

### 1.3 Fragility
- **Definition**: Blindly trusting external data or structures (e.g., assuming a list always has elements or a dictionary always has a specific key), leading to crashes on marginal edge cases.
- **Negative Example**:
  ```python
  def get_first_item_name(data: dict):
      return data['response']['items'][0]['name']
  ```
- **Positive Example**:
  ```python
  from typing import Optional
  def get_first_item_name(data: dict) -> Optional[str]:
      try:
          items = data.get('response', {}).get('items', [])
          if items and isinstance(items, list):
              return items[0].get('name')
      except (TypeError, AttributeError):
          pass
      return None
  ```
- **Guideline**: Must use safe access methods (e.g., `.get()`, length checks, type checking).

### 1.4 Inefficiency/Obsolescence
- **Definition**: Using inefficient algorithmic structures, such as N+1 query problems, or making I/O blocking calls inside loops without batch processing.
- **Negative Example**:
  ```python
  def delete_all_users(user_ids: list):
      for uid in user_ids:
          db.execute(f"DELETE FROM users WHERE id = {uid}")
  ```
- **Positive Example**:
  ```python
  def delete_users_bulk(user_ids: list):
      if not user_ids: return
      format_strings = ','.join(['%s'] * len(user_ids))
      db.execute(f"DELETE FROM users WHERE id IN ({format_strings})", tuple(user_ids))
  ```
- **Guideline**: When involving I/O (Database, Network), Bulk/Batch operations must be prioritized.

### 1.5 Instability
- **Definition**: Calling unstable external services (like network requests) without retry mechanisms or timeout settings.
- **Negative Example**:
  ```python
  def fetch_data(url: str):
      return requests.get(url).json() # Can hang forever or crash on random 503s
  ```
- **Positive Example**:
  ```python
  import requests
  from requests.adapters import HTTPAdapter
  from urllib3.util.retry import Retry

  def fetch_data(url: str, timeout: int = 10) -> dict:
      session = requests.Session()
      retries = Retry(total=3, backoff_factor=1, status_forcelist=[ 502, 503, 504 ])
      session.mount('http://', HTTPAdapter(max_retries=retries))
      session.mount('https://', HTTPAdapter(max_retries=retries))
      
      response = session.get(url, timeout=timeout)
      response.raise_for_status()
      return response.json()
  ```
- **Guideline**: Network requests must set an explicit `timeout` and optionally wrap retry mechanisms.

### 1.6 Spaghetti Code
- **Definition**: Using deeply nested `if-else` chains to handle a series of heterogeneous problems, making the code hard to maintain.
- **Negative Example**:
  ```python
  def connect(db_type: str):
      if db_type == "mysql":
          # 20 lines of mysql init
      elif db_type == "redis":
          # 20 lines of redis init
      elif db_type == "mongo":
          # ...
  ```
- **Guideline**: The checker should require splitting this into polymorphic structures, extracting separate initialization functions `init_mysql()`, `init_redis()`, or using a Strategy Pattern.

### 1.7 Tight Coupling
- **Definition**: Core business logic is strictly bound to specific side effects (like printing to console, returning specific framework views, or specific file writings).
- **Negative Example**:
  ```python
  def calculate_and_save_report(data: list):
      result = sum(data)
      with open('/tmp/report.txt', 'w') as f:
          f.write(str(result))
  ```
- **Positive Example**:
  ```python
  def calculate_report(data: list) -> float:
      return sum(data)

  def save_report(result: float, file_path: str):
      with open(file_path, 'w') as f:
          f.write(str(result))
  ```
- **Guideline**: Data processing logic must not be forcefully coupled with I/O actions. Ensure `calculate` is a Pure Function.

### 1.8 Low-level Repetition
- **Definition**: Failing to encapsulate a frequently used combination of existing operations into high-level primitives.
- **Guideline**: If a process always follows: cleaning -> filtering -> transformation, it should be encapsulated into a single `Workflow Pipeline` function, instead of making the caller write three lines of repeated calls every time.

---

## 2. Usability: Validating Functions and Writing Tests

In addition to statically reviewing the code, the Test Engineer Agent must dynamically verify usability by writing and executing **Python Unit Tests (Pytest)**.

### 2.1 Dependency Isolation (Mocking)
Skills often involve various platforms/environments (e.g., calling GitHub API directly, connecting to real DBs).
- **Rule**: Never initiate real external network requests in test cases (external APIs may have rate limits, and network instability causes flaky tests).
- **Execution**: You must use `unittest.mock.patch` or `responses`/`requests-mock`.
- **Example**:
  ```python
  from unittest.mock import patch
  
  @patch('requests.get')
  def test_get_stock_data(mock_get):
      mock_get.return_value.json.return_value = {"price": 150}
      mock_get.return_value.status_code = 200
      
      result = get_stock_data("AAPL")
      assert result["price"] == 150
      mock_get.assert_called_once_with("https://api.finance.com/v1/stock/AAPL", timeout=10)
  ```

### 2.2 Deterministic Assertions
- **Rule**: Tests must output deterministic Boolean pass/fail results. Human-eye verification using `print()` is strictly forbidden.
- **Execution**: Strictly check returned data structures, types, and boundary values using `assert`.

### 2.3 Edge Cases & Exception Coverage
- **Rule**: Do not just test the Happy Path. You must construct at least one exception scenario.
- **Execution**: Use `pytest.raises` to test whether it behaves as expected upon receiving invalid parameters or simulated API errors (e.g., silently returning `None` or raising custom encapsulated exceptions).
- **Example**:
  ```python
  import pytest
  from requests.exceptions import RequestException

  @patch('requests.get')
  def test_get_stock_data_timeout(mock_get):
      mock_get.side_effect = RequestException("Timeout")
      with pytest.raises(RequestException):
          get_stock_data("AAPL")
  ```

### 2.4 Isolated Environment
- **Rule**: If the code requires read/write file operations, the test must use Python's built-in `tempfile` or Pytest's `tmp_path` fixture. Writing side-effect files directly to a user's `/home` directory or the current working directory is strictly prohibited.
- **Example**:
  ```python
  def test_save_report(tmp_path):
      file_path = tmp_path / "test_report.txt"
      save_report(150.0, str(file_path))
      assert file_path.read_text() == "150.0"
  ```

### 2.5 Docstring & Type Hint Enforcement
- The Reviewer Agent must blindly verify that extracted code has complete **Type Hints**.
- It must also verify standard **Docstrings** are present (explaining the purpose, arguments, return values, and ideally containing `>>>` examples). If missing, the skill must be rejected and sent back for supplements.
