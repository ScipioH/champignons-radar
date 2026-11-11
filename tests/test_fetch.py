"""Tests du téléchargeur de sources (réseau simulé)."""
import hashlib
import io

import pytest

from radar import fetch

PAYLOAD = b"contenu-de-test" * 1000


class FakeResp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def opener_for(payload):
    calls = []

    def op(req, timeout=0):
        calls.append(req.full_url)
        return FakeResp(payload)

    op.calls = calls
    return op


def spec(**kw):
    s = {"url": "https://exemple.invalid/f.bin", "dest": "x/f.bin", "size": len(PAYLOAD),
         "md5": hashlib.md5(PAYLOAD).hexdigest()}
    s.update(kw)
    return s


def test_telechargement_ok_et_checksums(tmp_path):
    op = opener_for(PAYLOAD)
    assert fetch.download("src", spec(), tmp_path, op) == "telecharge"
    assert (tmp_path / "x" / "f.bin").read_bytes() == PAYLOAD
    assert not list(tmp_path.rglob("*.part"))
    sums = fetch._load_checksums(tmp_path)
    assert sums["src"]["sha256"] == hashlib.sha256(PAYLOAD).hexdigest()


def test_reprise_saute_si_conforme(tmp_path):
    op = opener_for(PAYLOAD)
    fetch.download("src", spec(), tmp_path, op)
    assert fetch.download("src", spec(), tmp_path, op) == "present"
    assert len(op.calls) == 1


def test_taille_incorrecte_rejetee_sans_residu(tmp_path):
    with pytest.raises(fetch.IntegrityError):
        fetch.download("src", spec(size=len(PAYLOAD) + 1, md5=None), tmp_path, opener_for(PAYLOAD))
    assert not (tmp_path / "x" / "f.bin").exists() and not list(tmp_path.rglob("*.part"))


def test_md5_incorrect_rejete(tmp_path):
    with pytest.raises(fetch.IntegrityError):
        fetch.download("src", spec(md5="0" * 32), tmp_path, opener_for(PAYLOAD))
    assert not (tmp_path / "x" / "f.bin").exists()


def test_fichier_corrompu_est_retelecharge(tmp_path):
    op = opener_for(PAYLOAD)
    fetch.download("src", spec(), tmp_path, op)
    (tmp_path / "x" / "f.bin").write_bytes(b"corrompu")
    assert fetch.download("src", spec(), tmp_path, op) == "telecharge"
    assert (tmp_path / "x" / "f.bin").read_bytes() == PAYLOAD


def test_source_inconnue(tmp_path):
    with pytest.raises(KeyError):
        fetch.fetch_all(only=["nope"], raw_dir=tmp_path, sources={"a": spec()})


def test_registre_reel_coherent():
    src = fetch.load_sources()
    assert len(src) == 14  # 2 BD Forêt + 2 géologie + 4 tuiles MNT + 4 cadastre + 2 DGFiP
    assert all(s["dest"].startswith("proprietes/") for n, s in src.items() if n.startswith(("cadastre", "dgfip")))
    for name, s in src.items():
        assert s["url"].startswith("https://"), name  # pas de HTTP en clair
        assert s["size"] > 0 and s["dest"], name
    assert all(len(s["md5"]) == 32 for n, s in src.items() if n.startswith("bdforet"))
