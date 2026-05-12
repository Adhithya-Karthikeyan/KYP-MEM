import chromadb
from pathlib import Path

class SessionMemory:
    def __init__(self, vault_path: str):
        self.db_path = Path(vault_path).parent / "chroma"
        self.db_path.mkdir(parents=True, exist_ok=True)
        self.client = chromadb.PersistentClient(path=str(self.db_path))
        self.collection = self.client.get_or_create_collection(name="sessions")

    def upsert_session(self, path: str, project: str, content: str):
        self.collection.upsert(
            documents=[content],
            metadatas=[{"project": project}],
            ids=[path]
        )

    def delete_session(self, path: str):
        try:
            self.collection.delete(ids=[path])
        except Exception:
            pass

    def search_sessions(self, query: str, project: str = None, n_results: int = 5):
        where = {"project": project} if project else None
        try:
            results = self.collection.query(
                query_texts=[query],
                n_results=n_results,
                where=where
            )
            return results
        except Exception:
            return {"ids": [], "documents": [], "metadatas": [], "distances": []}

session_memory = None

def init_vector_db(vault_path: str):
    global session_memory
    session_memory = SessionMemory(vault_path)

def get_session_memory():
    return session_memory
