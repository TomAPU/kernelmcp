import json


class Config:
    def __init__(self, path: str):
        self.path = path
        self.data = self.load_config()

    def load_config(self):
        try:
            with open(self.path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"ERROR: cannot read config file {self.path}: {e}")
            return {}
