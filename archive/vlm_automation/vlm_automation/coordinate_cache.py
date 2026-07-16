"""
Coordinate cache for VLM-discovered UI elements.
Caches coordinates keyed by (window_name, theme, window_size, element_name)
so that after the first VLM lookup, subsequent clicks are instant.
"""

import json
import time
import hashlib
from pathlib import Path
from typing import Optional, Dict, Any, Tuple


class CoordinateCache:
    """Cache for VLM-discovered coordinates."""

    def __init__(self, cache_file: Optional[str] = None):
        if cache_file is None:
            cache_file = str(Path.home() / '.openclaw' / 'workspace' / 'vlm-gui-automation' / 'coords_cache.json')
        self.cache_file = Path(cache_file)
        self.cache: Dict[str, Any] = {}
        self.load()

    def _make_key(self, window_name: str, window_size: Tuple[int, int], element: str, theme: str = "default") -> str:
        """Create cache key from context."""
        key_data = f"{window_name}|{window_size[0]}x{window_size[1]}|{theme}|{element}"
        return hashlib.sha256(key_data.encode()).hexdigest()[:16]

    def get(self, window_name: str, window_size: Tuple[int, int], element: str, theme: str = "default") -> Optional[Dict[str, Any]]:
        """Get cached coordinates for an element."""
        key = self._make_key(window_name, window_size, element, theme)
        entry = self.cache.get(key)
        if entry:
            entry['last_accessed'] = time.time()
            return entry
        return None

    def set(self, window_name: str, window_size: Tuple[int, int], element: str,
            coordinates: Tuple[int, int], action: str = 'click',
            theme: str = "default", confidence: float = 1.0):
        """Cache coordinates for an element."""
        key = self._make_key(window_name, window_size, element, theme)
        self.cache[key] = {
            'window_name': window_name,
            'window_size': window_size,
            'element': element,
            'theme': theme,
            'coordinates': coordinates,
            'action': action,
            'confidence': confidence,
            'created': time.time(),
            'last_accessed': time.time(),
            'hit_count': 0
        }
        self.save()

    def hit(self, window_name: str, window_size: Tuple[int, int], element: str, theme: str = "default"):
        """Record a cache hit."""
        key = self._make_key(window_name, window_size, element, theme)
        if key in self.cache:
            self.cache[key]['hit_count'] = self.cache[key].get('hit_count', 0) + 1
            self.cache[key]['last_accessed'] = time.time()

    def load(self):
        """Load cache from disk."""
        if self.cache_file.exists():
            try:
                with open(self.cache_file, 'r') as f:
                    self.cache = json.load(f)
            except Exception as e:
                print(f"Warning: Could not load cache: {e}")
                self.cache = {}

    def save(self):
        """Save cache to disk."""
        try:
            self.cache_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.cache_file, 'w') as f:
                json.dump(self.cache, f, indent=2)
        except Exception as e:
            print(f"Warning: Could not save cache: {e}")

    def clear(self):
        """Clear all cached entries."""
        self.cache = {}
        self.save()

    def list_entries(self) -> list:
        """List all cache entries."""
        return [
            {
                'element': v['element'],
                'window': v['window_name'],
                'size': f"{v['window_size'][0]}x{v['window_size'][1]}",
                'coords': v['coordinates'],
                'hits': v.get('hit_count', 0)
            }
            for v in self.cache.values()
        ]


if __name__ == '__main__':
    import sys
    cache = CoordinateCache()

    if len(sys.argv) < 2:
        print("Usage: python coordinate_cache.py <command> [args...]")
        print("Commands: list, clear, get <window> <size> <element>, set <window> <size> <element> <x> <y>")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == 'list':
        entries = cache.list_entries()
        if not entries:
            print("Cache is empty")
        else:
            for e in entries:
                print(f"  {e['element']} @ {e['window']} ({e['size']}) -> {e['coords']} (hits: {e['hits']})")

    elif cmd == 'clear':
        cache.clear()
        print("Cache cleared")

    elif cmd == 'get':
        window = sys.argv[2]
        size = tuple(map(int, sys.argv[3].split('x')))
        element = sys.argv[4]
        result = cache.get(window, size, element)
        print(result if result else "Not found")

    elif cmd == 'set':
        window = sys.argv[2]
        size = tuple(map(int, sys.argv[3].split('x')))
        element = sys.argv[4]
        x, y = int(sys.argv[5]), int(sys.argv[6])
        cache.set(window, size, element, (x, y))
        print(f"Cached {element} -> ({x}, {y})")
