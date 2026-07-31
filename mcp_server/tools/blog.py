"""Tools MCP do blog (KL-133) — publicação editorial via Claude.

O conteúdo (markdown) vive no banco (`blog_posts`); estas tools criam/editam/listam os
posts. `status='draft'` (padrão) não aparece no site; `status='published'` publica e seta
`published_at`. O slug é gerado do título; `reading_time_min` é calculado (ceil(palavras/200)).
"""
from __future__ import annotations

from typing import Optional

from mcp_server._base import mcp, _guard, _store


def _ser(post: Optional[dict]) -> Optional[dict]:
    """Serializa datetimes → ISO e normaliza tags (o retorno da tool vira JSON)."""
    if not post:
        return post
    out = dict(post)
    for k in ("published_at", "created_at", "updated_at"):
        v = out.get(k)
        if hasattr(v, "isoformat"):
            out[k] = v.isoformat()
    out["tags"] = list(out.get("tags") or [])
    return out


@mcp.tool()
async def create_blog_post(title: str, content: str, category: str = "seguranca",
                           tags: Optional[list] = None, meta_description: Optional[str] = None,
                           subtitle: Optional[str] = None, data_snapshot: Optional[dict] = None,
                           status: str = "draft", og_image_url: Optional[str] = None) -> dict:
    """Cria um post no blog (conteúdo em **markdown**). Default `status='draft'` (não aparece no
    site); use `status='published'` para publicar direto. `category`: seguranca/lgpd/dados/setor/
    tutorial. `data_snapshot`: dados proprietários (dict) usados no artigo. O slug é gerado do
    título e o tempo de leitura é calculado. Retorna o post criado (com id + slug)."""
    async def _impl():
        return _ser(await _store().create_blog_post(
            title=title, content=content, category=category, tags=tags,
            meta_description=meta_description, subtitle=subtitle,
            data_snapshot=data_snapshot, status=status, og_image_url=og_image_url))

    return await _guard(_impl)


@mcp.tool()
async def update_blog_post(post_id: int, title: Optional[str] = None, content: Optional[str] = None,
                           status: Optional[str] = None, meta_description: Optional[str] = None,
                           tags: Optional[list] = None, category: Optional[str] = None,
                           subtitle: Optional[str] = None, data_snapshot: Optional[dict] = None,
                           og_image_url: Optional[str] = None) -> dict:
    """Atualiza um post existente (campos omitidos NÃO são alterados). Mudar para
    `status='published'` seta `published_at` (se ainda nulo); mudar o conteúdo recalcula o
    tempo de leitura. Retorna o post atualizado (ou erro se o id não existe)."""
    async def _impl():
        fields = {k: v for k, v in {
            "title": title, "content": content, "status": status,
            "meta_description": meta_description, "tags": tags, "category": category,
            "subtitle": subtitle, "data_snapshot": data_snapshot, "og_image_url": og_image_url,
        }.items() if v is not None}
        post = await _store().update_blog_post(post_id, **fields)
        return _ser(post) if post else {"error": "post não encontrado", "id": post_id}

    return await _guard(_impl)


@mcp.tool()
async def list_blog_posts(status: Optional[str] = None, limit: int = 20) -> dict:
    """Lista posts do blog (todos os status: draft/published/archived). Filtro opcional por
    `status`. Retorna `{posts: [...], total}` (sem o corpo markdown, só metadados)."""
    async def _impl():
        res = await _store().list_all_blog_posts(page=1, per_page=min(max(limit, 1), 100),
                                                  status=status)
        posts = []
        for p in res["posts"]:
            d = _ser(p)
            d.pop("content", None)
            d.pop("data_snapshot", None)
            posts.append(d)
        return {"posts": posts, "total": res["total"]}

    return await _guard(_impl)


@mcp.tool()
async def get_blog_post(post_id: Optional[int] = None, slug: Optional[str] = None) -> dict:
    """Busca um post por `post_id` OU `slug` (qualquer status — visão admin). Retorna o post
    completo (com o corpo markdown) ou um erro se não encontrado."""
    async def _impl():
        store = _store()
        if post_id is not None:
            post = await store.get_blog_post_by_id(post_id)
        elif slug:
            post = await store.get_blog_post_by_slug(slug, published_only=False)
        else:
            return {"error": "informe post_id ou slug"}
        return _ser(post) if post else {"error": "post não encontrado"}

    return await _guard(_impl)


@mcp.tool()
async def archive_blog_post(post_id: int) -> dict:
    """Arquiva um post (soft delete → `status='archived'`, some do site). Retorna o post."""
    async def _impl():
        post = await _store().archive_blog_post(post_id)
        return _ser(post) if post else {"error": "post não encontrado", "id": post_id}

    return await _guard(_impl)
