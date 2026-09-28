class Catalog:
    def __init__(self):
        self.items = {}
        self.revoked = set()

    def publish(self, namespace, name, version, content_id, metadata=None):
        self.items[(namespace, name, version)] = {
            "content_id": content_id,
            "metadata": metadata or {},
        }
        return self.items[(namespace, name, version)]

    def revoke(self, namespace, name, version):
        self.revoked.add((namespace, name, version))

    def search(self, text):
        return [
            (*k, v)
            for k, v in self.items.items()
            if text.lower() in "/".join(k).lower() and k not in self.revoked
        ]
