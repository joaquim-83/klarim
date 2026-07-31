"""KL-133 — blog editorial: store helpers + endpoints públicos/admin + RSS + sitemap.

Offline (FakeStore em memória replicando a semântica do SQL + TestClient). O SQL real
(INSERT/UPDATE/tags[]/jsonb) é validado na VM.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

import api.main as m
from discovery.store import _blog_slugify, _blog_reading_time

_STATUSES = ("draft", "published", "archived")


# --------------------------- helpers puros -------------------------------- #

def test_slugify():
    assert _blog_slugify("Meu Site é Seguro? Análise!") == "meu-site-e-seguro-analise"
    assert _blog_slugify("  Espaços   e --- hífens ") == "espacos-e-hifens"
    assert _blog_slugify("") == "post"


@pytest.mark.parametrize("words,expected", [(0, 1), (200, 1), (201, 2), (500, 3), (1000, 5)])
def test_reading_time(words, expected):
    assert _blog_reading_time(" ".join(["x"] * words)) == expected


# --------------------------- FakeStore ------------------------------------ #

class FakeBlogStore:
    def __init__(self):
        self.posts, self._id = {}, 1

    async def create_blog_post(self, title, content, slug=None, subtitle=None,
                               meta_description=None, og_image_url=None, category="seguranca",
                               tags=None, status="draft", author="Klarim", data_snapshot=None):
        slug = _blog_slugify(slug or title)
        if any(p["slug"] == slug for p in self.posts.values()):
            raise ValueError("duplicate slug")   # replica a UNIQUE do Postgres
        now = datetime.now(timezone.utc)
        pid = self._id
        self._id += 1
        p = {"id": pid, "slug": slug, "title": title, "subtitle": subtitle, "content": content,
             "meta_description": meta_description, "og_image_url": og_image_url,
             "category": category, "tags": list(tags or []),
             "status": status if status in _STATUSES else "draft", "author": author,
             "data_snapshot": data_snapshot, "reading_time_min": _blog_reading_time(content),
             "published_at": now if status == "published" else None,
             "created_at": now, "updated_at": now}
        self.posts[pid] = p
        return dict(p)

    async def update_blog_post(self, post_id, **f):
        p = self.posts.get(post_id)
        if not p:
            return None
        for k in ("title", "subtitle", "content", "meta_description", "og_image_url",
                  "category", "author"):
            if f.get(k) is not None:
                p[k] = f[k]
        if f.get("slug"):
            p["slug"] = _blog_slugify(f["slug"])
        if f.get("content") is not None:
            p["reading_time_min"] = _blog_reading_time(f["content"])
        if f.get("tags") is not None:
            p["tags"] = list(f["tags"])
        if f.get("data_snapshot") is not None:
            p["data_snapshot"] = f["data_snapshot"]
        st = f.get("status")
        if st in _STATUSES:
            p["status"] = st
            if st == "published" and not p["published_at"]:
                p["published_at"] = datetime.now(timezone.utc)
        p["updated_at"] = datetime.now(timezone.utc)
        return dict(p)

    async def archive_blog_post(self, post_id):
        p = self.posts.get(post_id)
        if not p:
            return None
        p["status"] = "archived"
        return dict(p)

    async def get_blog_post_by_id(self, post_id):
        p = self.posts.get(post_id)
        return dict(p) if p else None

    async def get_blog_post_by_slug(self, slug, published_only=True):
        for p in self.posts.values():
            if p["slug"] == slug and (not published_only or p["status"] == "published"):
                return dict(p)
        return None

    async def list_published_blog_posts(self, page=1, per_page=10, category=None):
        pub = [p for p in self.posts.values() if p["status"] == "published"
               and (not category or p["category"] == category)]
        pub.sort(key=lambda x: x["published_at"] or datetime.min.replace(tzinfo=timezone.utc),
                 reverse=True)
        off = (page - 1) * per_page
        return {"posts": [dict(p) for p in pub[off:off + per_page]], "total": len(pub),
                "page": page, "per_page": per_page}

    async def list_all_blog_posts(self, page=1, per_page=20, status=None):
        allp = [p for p in self.posts.values() if (not status or p["status"] == status)]
        allp.sort(key=lambda x: x["created_at"], reverse=True)
        off = (page - 1) * per_page
        return {"posts": [dict(p) for p in allp[off:off + per_page]], "total": len(allp),
                "page": page, "per_page": per_page}


@pytest.fixture
def store():
    return FakeBlogStore()


@pytest.fixture
def client(store, monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "x" * 64)
    monkeypatch.setenv("ADMIN_USER", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "pw")
    monkeypatch.setattr(m, "get_target_store", lambda: store)
    m._blog_rl_hits.clear()
    m._blog_admin_rl_hits.clear()
    return TestClient(m.app, raise_server_exceptions=False)


def _admin():
    return {"Authorization": f"Bearer {m._create_token('admin')}"}


# --------------------------- admin CRUD ----------------------------------- #

def test_admin_create_requires_auth(client):
    assert client.post("/admin/blog/posts", json={"title": "x", "content": "y"}).status_code == 401


def test_admin_create_then_public_flow(client, store):
    # cria draft
    r = client.post("/admin/blog/posts", headers=_admin(),
                    json={"title": "Meu Site é Seguro?", "content": " ".join(["w"] * 400),
                          "category": "seguranca", "tags": ["t"]})
    assert r.status_code == 201
    d = r.json()
    assert d["slug"] == "meu-site-e-seguro" and d["status"] == "draft"
    assert d["reading_time_min"] == 2 and d["published_at"] is None
    pid = d["id"]

    # draft NÃO aparece no público
    assert client.get(f"/blog/posts/{d['slug']}").status_code == 404
    assert client.get("/blog/posts").json()["total"] == 0

    # publica
    r = client.put(f"/admin/blog/posts/{pid}", headers=_admin(), json={"status": "published"})
    assert r.status_code == 200 and r.json()["status"] == "published"
    assert r.json()["published_at"] is not None

    # agora aparece no público + RSS + sitemap
    assert client.get(f"/blog/posts/{d['slug']}").status_code == 200
    lst = client.get("/blog/posts").json()
    assert lst["total"] == 1 and lst["posts"][0]["slug"] == d["slug"]
    assert "content" not in lst["posts"][0]   # a lista não traz o corpo


def test_admin_create_duplicate_slug_409(client):
    body = {"title": "Título Único", "content": "abc"}
    assert client.post("/admin/blog/posts", headers=_admin(), json=body).status_code == 201
    assert client.post("/admin/blog/posts", headers=_admin(), json=body).status_code == 409


def test_admin_create_missing_fields_422(client):
    assert client.post("/admin/blog/posts", headers=_admin(),
                       json={"title": "só título"}).status_code == 422


def test_admin_archive_removes_from_public(client, store):
    pid = client.post("/admin/blog/posts", headers=_admin(),
                      json={"title": "A publicar", "content": "abc", "status": "published"}).json()["id"]
    slug = list(store.posts.values())[0]["slug"]
    assert client.get(f"/blog/posts/{slug}").status_code == 200
    assert client.delete(f"/admin/blog/posts/{pid}", headers=_admin()).status_code == 200
    assert client.get(f"/blog/posts/{slug}").status_code == 404   # arquivado some


# --------------------------- público: RSS + sitemap ----------------------- #

def _publish(client, title, content="conteúdo do post aqui"):
    pid = client.post("/admin/blog/posts", headers=_admin(),
                      json={"title": title, "content": content, "meta_description": "desc " + title,
                            "status": "published"}).json()["id"]
    return pid


def test_blog_rss_is_valid(client):
    _publish(client, "Post Um")
    _publish(client, "Post Dois")
    r = client.get("/blog/rss.xml")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/rss+xml")
    body = r.text
    assert "<rss" in body and "<channel>" in body and body.count("<item>") == 2
    assert "https://klarim.net/blog/post-um" in body and "<pubDate>" in body


def test_sitemap_blog(client):
    _publish(client, "Artigo SEO")
    r = client.get("/sitemap-blog.xml")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/xml")
    assert "<loc>https://klarim.net/blog/artigo-seo</loc>" in r.text


def test_blog_list_pagination_and_category(client):
    _publish(client, "Sec 1")
    _publish(client, "Sec 2")
    # filtro por categoria inexistente → vazio
    assert client.get("/blog/posts?category=lgpd").json()["total"] == 0
    r = client.get("/blog/posts?per_page=1&page=1").json()
    assert r["per_page"] == 1 and r["total"] == 2 and r["has_more"] is True
