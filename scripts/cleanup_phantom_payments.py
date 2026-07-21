"""KL-93 — remove as cobranças PIX fantasma criadas durante o teste de segurança.

O `POST /payment/create` criava cobrança PIX **real** sem autenticação/validação; a
varredura de segurança gerou 2 cobranças de teste. Este script as apaga por `charge_id`
(idempotente — apagar 2x não faz nada). Roda na VM (usa os `POSTGRES_*` do .env):

    docker compose exec -T api python -m scripts.cleanup_phantom_payments

Não recebe argumentos externos (os alvos são fixos e conhecidos) — nada de SQL dinâmico.
"""

from __future__ import annotations

import asyncio

# Cobranças fantasma do teste de segurança (KL-93). Apagar por charge_id é inequívoco
# e idempotente (o id serial pode variar entre ambientes; o charge_id é único).
PHANTOM_CHARGE_IDS = (
    "pix_char_jWePxHqsFXNPy3wDNHkAac3T",   # id 16 — klarim.net
    "pix_char_wDYLwbyR3HQSDLLNgCUNkLtg",   # id 17 — dominioinventado123456.com.br
)


async def main() -> None:
    from payments.store import get_store

    store = get_store()
    total = 0
    for charge_id in PHANTOM_CHARGE_IDS:
        # "Olhar antes de apagar": mostra o que será removido (URL/e-mail/status/valor).
        try:
            charge = await store.get(charge_id)
        except Exception as exc:  # noqa: BLE001
            print(f"[cleanup] falha ao ler {charge_id}: {exc!r}", flush=True)
            charge = None
        if charge is None:
            print(f"[cleanup] {charge_id}: não encontrada (já removida?) — pulando.", flush=True)
            continue
        print(f"[cleanup] removendo {charge_id}: url={charge.target_url!r} "
              f"email={charge.buyer_email!r} status={charge.status} "
              f"amount_cents={charge.amount_cents}", flush=True)
        try:
            n = await store.delete(charge_id)
        except Exception as exc:  # noqa: BLE001
            print(f"[cleanup] falha ao apagar {charge_id}: {exc!r}", flush=True)
            continue
        total += n
        print(f"[cleanup] {charge_id}: {n} linha(s) removida(s)", flush=True)
    print(f"[cleanup] concluído — {total} cobrança(s) fantasma removida(s).", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
