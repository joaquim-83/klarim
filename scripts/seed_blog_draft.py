"""KL-133 — cria 1 artigo de teste (DRAFT) para validar o fluxo do blog.

NÃO publica (draft não aparece no site). Idempotente: se o slug já existe, não recria.
Uso (na VM, dentro do container `api`):
    docker compose exec api python -m scripts.seed_blog_draft
"""
from __future__ import annotations

import asyncio

from discovery.store import get_target_store, _blog_slugify

_TITLE = "Teste do sistema de blog"
_CONTENT = """# Teste

Este é um artigo de teste para validar o sistema de blog.

## Funcionalidades testadas

- Markdown rendering
- Tabelas
- Código

| Header 1 | Header 2 |
|---|---|
| Cell 1 | Cell 2 |

```python
print('hello world')
```
"""


async def main() -> None:
    store = get_target_store()
    await store.ensure_schema()
    slug = _blog_slugify(_TITLE)
    existing = await store.get_blog_post_by_slug(slug, published_only=False)
    if existing:
        print(f"[blog] draft já existe (id={existing['id']}, status={existing['status']}); nada a fazer.")
        return
    post = await store.create_blog_post(
        title=_TITLE, content=_CONTENT, category="seguranca",
        tags=["teste", "infraestrutura"],
        meta_description="Artigo de teste do sistema de blog da Klarim.",
        status="draft")
    print(f"[blog] draft criado: id={post['id']} slug={post['slug']} "
          f"reading_time={post['reading_time_min']}min (status=draft, NÃO publicado).")


if __name__ == "__main__":
    asyncio.run(main())
