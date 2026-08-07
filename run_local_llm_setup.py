import time
import os
from local_llm_manager import ensure_local_llm_ready, is_ollama_installed, is_ollama_ready, _flag_path
print('precheck: ollama_installed=', is_ollama_installed())
ok = ensure_local_llm_ready(ui=None, config_path='config/llm_config.json')
print('ensure_local_llm_ready returned:', ok)
print('postcheck: ollama_installed=', is_ollama_installed())
print('postcheck: ollama_ready=', is_ollama_ready())
print('flag exists:', os.path.exists(_flag_path()))
