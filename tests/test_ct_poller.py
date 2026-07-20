"""Testes do poller de CT logs (KL-15) — offline, com cert real gerado na hora."""

from __future__ import annotations

import base64
import datetime

from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from discovery.ct_client import normalize_domain
from discovery.ct_poller import extract_san_domains, CTLogPoller


# --- normalize_domain (compartilhado crt.sh + poller) ---------------------- #

def test_normalize_domain():
    assert normalize_domain("Loja.COM.BR") == "loja.com.br"
    assert normalize_domain("*.hotelx.com.br") == "hotelx.com.br"
    assert normalize_domain("www.hotelx.com.br") == "hotelx.com.br"
    assert normalize_domain("mail.hotelx.com.br") is None       # infra
    assert normalize_domain("example.com") is None              # não .com.br
    assert normalize_domain("com espaco.com.br") is None
    assert normalize_domain("") is None


# --- parsing de CT: cert real → DER → MerkleTreeLeaf → SANs ----------------- #

def _make_cert(sans):
    key = ec.generate_private_key(ec.SECP256R1())
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, sans[0])])
    builder = (
        x509.CertificateBuilder()
        .subject_name(subject).issuer_name(issuer).public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime(2026, 1, 1))
        .not_valid_after(datetime.datetime(2027, 1, 1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(d) for d in sans]), critical=False)
    )
    return builder.sign(key, hashes.SHA256())


def _x509_leaf_entry(cert):
    """Monta uma entrada get-entries (x509_entry) com o cert dado."""
    der = cert.public_bytes(serialization.Encoding.DER)
    leaf = (
        b"\x00"              # version
        b"\x00"             # leaf_type = timestamped_entry
        + b"\x00" * 8         # timestamp
        + b"\x00\x00"        # entry_type = 0 (x509)
        + len(der).to_bytes(3, "big")
        + der
    )
    return {"leaf_input": base64.b64encode(leaf).decode(), "extra_data": ""}


def test_extract_san_domains_from_x509_entry():
    cert = _make_cert(["loja.com.br", "www.loja.com.br"])
    entry = _x509_leaf_entry(cert)
    domains = extract_san_domains(entry)
    assert "loja.com.br" in domains and "www.loja.com.br" in domains


def test_extract_san_domains_bad_entry():
    assert extract_san_domains({"leaf_input": "not-base64!!", "extra_data": ""}) == []
    assert extract_san_domains({}) == []


# --- pipeline de ingestão + buffer ----------------------------------------- #

def test_ingest_filters_and_buffers():
    p = CTLogPoller()
    cert = _make_cert(["loja.com.br", "mail.loja.com.br", "site.com", "outro.com.br"])
    p._ingest(_x509_leaf_entry(cert))
    assert p.total_seen == 1
    buf = set(p.flush_buffer())
    assert buf == {"loja.com.br", "outro.com.br"}   # mail. (infra) e .com fora
    assert p.flush_buffer() == []                    # já limpo


def test_buffer_cap_respected():
    p = CTLogPoller()
    p.max_buffer = 1
    cert = _make_cert(["a.com.br", "b.com.br", "c.com.br"])
    p._ingest(_x509_leaf_entry(cert))
    assert len(p.flush_buffer()) == 1


def test_get_stats_shape():
    p = CTLogPoller()
    s = p.get_stats()
    assert set(s) == {"connected", "last_event_at", "total_seen", "total_matched",
                      "buffer_size", "subdomain_buffer_size"}
    assert s["connected"] is False
