import math
import re


class LocalDocumentRAG:
    """Simple lexical retriever for local generated docs."""

    def __init__(self, documents, chunk_size=1200, overlap=200):
        self.documents = documents or {}
        self.chunk_size = max(300, chunk_size)
        self.overlap = max(0, min(overlap, self.chunk_size // 2))
        self.chunks = self._build_chunks()

    def _build_chunks(self):
        chunks = []
        for path, content in self.documents.items():
            text = (content or "").strip()
            if not text:
                continue

            start = 0
            index = 0
            while start < len(text):
                end = min(len(text), start + self.chunk_size)
                chunk_text = text[start:end].strip()
                if chunk_text:
                    chunks.append(
                        {
                            "path": path,
                            "index": index,
                            "text": chunk_text,
                            "tokens": self._tokenize(chunk_text),
                        }
                    )
                if end >= len(text):
                    break
                start = max(start + 1, end - self.overlap)
                index += 1
        return chunks

    def _tokenize(self, text):
        return [token for token in re.split(r"[^A-Za-z0-9_]+", (text or "").lower()) if token]

    def _score(self, query_tf, chunk_tokens):
        if not chunk_tokens:
            return 0.0

        chunk_tf = {}
        for token in chunk_tokens:
            chunk_tf[token] = chunk_tf.get(token, 0) + 1

        score = 0.0
        for token, q_count in query_tf.items():
            c_count = chunk_tf.get(token, 0)
            if c_count:
                score += q_count * (1.0 + math.log(1 + c_count))
        return score

    def retrieve(self, query, top_k=4):
        query_tokens = self._tokenize(query)
        if not query_tokens:
            return self.chunks[:top_k]

        query_tf = {}
        for token in query_tokens:
            query_tf[token] = query_tf.get(token, 0) + 1

        scored = []
        for chunk in self.chunks:
            score = self._score(query_tf, chunk["tokens"])
            if score > 0:
                scored.append((score, chunk))

        scored.sort(key=lambda item: item[0], reverse=True)
        return [chunk for _, chunk in scored[:top_k]]

    def render(self, query, top_k=4):
        matches = self.retrieve(query=query, top_k=top_k)
        if not matches:
            return ""

        blocks = []
        for item in matches:
            blocks.append(
                f"=== Retrieved Doc: {item['path']} [chunk {item['index']}] ===\n{item['text']}"
            )
        return "\n\n".join(blocks)
