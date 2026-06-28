"""
Ollama API client for VLM queries.
Supports both local Ollama and Ollama Cloud.
"""

import json
import base64
import requests
from pathlib import Path
from typing import Optional, List, Dict, Any


class OllamaClient:
    """Client for Ollama API (local or cloud)."""

    def __init__(self, base_url: str = "http://localhost:11434", api_key: Optional[str] = None, timeout: float = 180.0):
        self.base_url = base_url.rstrip('/')
        self.api_key = api_key
        self.is_cloud = 'ollama.com' in base_url or 'api.ollama.ai' in base_url
        self.timeout = timeout

    def _get_headers(self) -> Dict[str, str]:
        headers = {'Content-Type': 'application/json'}
        if self.api_key:
            headers['Authorization'] = f'Bearer {self.api_key}'
        return headers

    def chat(self, model: str, messages: List[Dict[str, Any]], stream: bool = False, options: Optional[Dict] = None, timeout: Optional[float] = None) -> Dict[str, Any]:
        """Send chat request to Ollama."""
        payload = {
            'model': model,
            'messages': messages,
            'stream': stream,
        }
        if options:
            payload['options'] = options

        req_timeout = timeout if timeout is not None else self.timeout
        resp = requests.post(
            f'{self.base_url}/api/chat',
            headers=self._get_headers(),
            json=payload,
            timeout=req_timeout
        )
        resp.raise_for_status()
        return resp.json()

    def chat_with_image(self, model: str, prompt: str, image_path: str, system: Optional[str] = None) -> str:
        """Send image + text to VLM and return response text."""
        with open(image_path, 'rb') as f:
            image_b64 = base64.b64encode(f.read()).decode('utf-8')

        content = prompt
        message = {
            'role': 'user',
            'content': content,
            'images': [image_b64]
        }

        messages = []
        if system:
            messages.append({'role': 'system', 'content': system})
        messages.append(message)

        result = self.chat(model, messages)
        return result.get('message', {}).get('content', '')

    def list_models(self) -> List[str]:
        """List available models."""
        try:
            resp = requests.get(f'{self.base_url}/api/tags', timeout=10)
            resp.raise_for_status()
            data = resp.json()
            return [m['name'] for m in data.get('models', [])]
        except Exception as e:
            print(f"Error listing models: {e}")
            return []

    def generate(self, model: str, prompt: str, images: Optional[List[str]] = None, options: Optional[Dict] = None) -> str:
        """Generate text (non-chat endpoint)."""
        payload = {
            'model': model,
            'prompt': prompt,
            'stream': False,
        }
        if images:
            payload['images'] = images
        if options:
            payload['options'] = options

        resp = requests.post(
            f'{self.base_url}/api/generate',
            headers=self._get_headers(),
            json=payload,
            timeout=180
        )
        resp.raise_for_status()
        return resp.json().get('response', '')


if __name__ == '__main__':
    import sys
    client = OllamaClient()

    if len(sys.argv) < 2:
        print("Usage: python ollama_client.py <command> [args...]")
        print("Commands: list, chat <model> <prompt>, vision <model> <prompt> <image_path>")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == 'list':
        models = client.list_models()
        print("Available models:")
        for m in models:
            print(f"  - {m}")

    elif cmd == 'chat':
        model = sys.argv[2]
        prompt = ' '.join(sys.argv[3:])
        result = client.chat(model, [{'role': 'user', 'content': prompt}])
        print(result['message']['content'])

    elif cmd == 'vision':
        model = sys.argv[2]
        prompt = sys.argv[3]
        image_path = sys.argv[4]
        result = client.chat_with_image(model, prompt, image_path)
        print(result)
