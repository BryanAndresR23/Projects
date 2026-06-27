---
name: webapp-testing
description: Automates testing of local web applications using Playwright in Python. Use for verifying frontend functionality, debugging UI behavior, capturing browser screenshots, and viewing browser logs.
source: https://github.com/ComposioHQ/awesome-claude-skills
---

# Web App Testing Toolkit

## Overview
Interact with and test local web applications using Playwright (Python). Supports verifying frontend functionality, debugging UI behavior, capturing screenshots, and viewing browser logs.

## Decision Tree

1. **Static HTML files?** → Read files directly, extract selectors, test with Playwright
2. **Dynamic webapp, no server running?** → Use `with_server.py` helper to manage server lifecycle
3. **Server already running?** → Go straight to Playwright reconnaissance + action execution

## Critical Rule
Always call `page.wait_for_load_state('networkidle')` before inspecting the DOM on dynamic applications. Inspecting before this call yields incomplete/stale DOM state.

## Standard Playwright Script Structure
```python
from playwright.sync_api import sync_playwright

def run():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        
        page.goto('http://localhost:3000')
        page.wait_for_load_state('networkidle')  # ALWAYS wait
        
        # Reconnaissance
        screenshot = page.screenshot(path='screenshot.png')
        html = page.content()
        
        # Actions
        page.click('button#submit')
        page.fill('input[name="email"]', 'test@example.com')
        
        # Assertions
        assert page.is_visible('.success-message')
        
        browser.close()

run()
```

## Multi-Server Helper (`with_server.py`)
```python
# Start server, run tests, stop server
python with_server.py --command "npm start" --port 3000 -- python test_suite.py
```

## Common Test Patterns

### Take Screenshot
```python
page.screenshot(path='debug.png', full_page=True)
```

### Check Console Logs
```python
logs = []
page.on('console', lambda msg: logs.append(msg.text))
page.goto(url)
page.wait_for_load_state('networkidle')
print('\n'.join(logs))
```

### Form Interaction
```python
page.fill('[name="username"]', 'testuser')
page.fill('[name="password"]', 'password123')
page.click('[type="submit"]')
page.wait_for_url('**/dashboard')
```

### Wait for Element
```python
page.wait_for_selector('.data-loaded', timeout=10000)
element = page.query_selector('.data-loaded')
text = element.text_content()
```

### Network Request Interception
```python
def handle_response(response):
    if '/api/' in response.url:
        print(f"{response.status} {response.url}")

page.on('response', handle_response)
```

## Browser Available
Chromium is pre-installed at `/opt/pw-browsers/chromium`. Use:
```python
browser = p.chromium.launch(
    executable_path='/opt/pw-browsers/chromium',
    headless=True
)
```
