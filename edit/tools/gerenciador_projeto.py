#!/usr/bin/env python3
"""
Utilitário de Gerenciamento do Projeto TARGETS_VIRTU
- Sincroniza 'edit/src/' -> 'backup/src/'
- Comprime 'edit/src/' -> 'data/*.gz' e 'data/*.br' (quando brotli disponível)
- Sincroniza 'edit/src/' -> 'android_app/www/' (Capacitor WebView)
- Exibe o status do repositório
"""

import gzip
import os
import shutil
import sys
from pathlib import Path
from typing import Optional

try:
    import brotli
except ImportError:
    brotli = None  # type: ignore[assignment]

BASE_DIR = Path(__file__).resolve().parent.parent.parent
EDIT_SRC = BASE_DIR / "edit" / "src"
BACKUP_SRC = BASE_DIR / "backup" / "src"
DATA_DIR = BASE_DIR / "data"
ANDROID_WWW = BASE_DIR / "android_app" / "www"
REGRAS_DIR = BASE_DIR / "Regras"
TEST_DIR = BASE_DIR / "test"
LIB_DIR = BASE_DIR / "lib"


def _espelhar_diretorio(src_dir: Path, dst_dir: Path) -> int:
    """Espelha exatamente src_dir -> dst_dir removendo arquivos obsoletos no destino."""
    if dst_dir.exists():
        shutil.rmtree(dst_dir)
    dst_dir.mkdir(parents=True, exist_ok=True)

    count = 0
    for root, _, files in os.walk(src_dir):
        rel_path = os.path.relpath(root, src_dir)
        target_dir = dst_dir if rel_path == "." else dst_dir / rel_path
        target_dir.mkdir(parents=True, exist_ok=True)
        for f in sorted(files):
            shutil.copy2(Path(root) / f, target_dir / f)
            count += 1
    return count


def fazer_backup() -> None:
    print(f"[*] Sincronizando backup: {EDIT_SRC} -> {BACKUP_SRC}...")
    count = _espelhar_diretorio(EDIT_SRC, BACKUP_SRC)
    print(f"[+] Backup concluído com sucesso ({count} arquivo(s) salvos em backup/src).")


def sincronizar_android() -> None:
    if not (BASE_DIR / "android_app").exists():
        print("[!] Pasta android_app/ não encontrada; pulando sincronização Capacitor.")
        return
    print(f"[*] Sincronizando WebView Android: {EDIT_SRC} -> {ANDROID_WWW}...")
    count = _espelhar_diretorio(EDIT_SRC, ANDROID_WWW)
    print(f"[+] Android www sincronizado com sucesso ({count} arquivo(s) em android_app/www).")


def comprimir_gz(brotli_mod: Optional[object] = None) -> None:
    """Comprime edit/src/ para data/*.gz e data/*.br (se brotli estiver disponível)."""
    active_brotli = brotli_mod if brotli_mod is not None else brotli
    has_brotli = active_brotli is not None
    mode_label = "*.gz + *.br" if has_brotli else "*.gz"
    print(f"[*] Comprimindo arquivos de produção: {EDIT_SRC} -> {DATA_DIR} ({mode_label})...")
    if not has_brotli:
        print("[!] Módulo 'brotli' não encontrado no ambiente; gerando apenas arquivos *.gz.")

    if DATA_DIR.exists():
        shutil.rmtree(DATA_DIR)
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    count = 0
    total_orig = 0
    total_gz = 0
    total_br = 0
    for root, _, files in os.walk(EDIT_SRC):
        rel_path = os.path.relpath(root, EDIT_SRC)
        dest_dir = DATA_DIR if rel_path == "." else DATA_DIR / rel_path
        dest_dir.mkdir(parents=True, exist_ok=True)
        for f in sorted(files):
            src_file = Path(root) / f
            raw_bytes = src_file.read_bytes()
            orig_size = len(raw_bytes)

            gz_file = dest_dir / f"{f}.gz"
            gz_bytes = gzip.compress(raw_bytes, compresslevel=9)
            gz_file.write_bytes(gz_bytes)
            gz_size = len(gz_bytes)

            total_orig += orig_size
            total_gz += gz_size
            gz_ratio = (100 - (gz_size / orig_size * 100)) if orig_size > 0 else 0.0

            if has_brotli:
                br_file = dest_dir / f"{f}.br"
                br_bytes = active_brotli.compress(raw_bytes, quality=11)  # type: ignore[attr-defined]
                br_file.write_bytes(br_bytes)
                br_size = len(br_bytes)
                total_br += br_size
                br_ratio = (100 - (br_size / orig_size * 100)) if orig_size > 0 else 0.0
                print(
                    f"  -> {f} ({orig_size} B) => {gz_file.name} ({gz_size} B, {gz_ratio:.1f}% menor) "
                    f"| {br_file.name} ({br_size} B, {br_ratio:.1f}% menor)"
                )
            else:
                print(f"  -> {f} ({orig_size} B) => {gz_file.name} ({gz_size} B) [{gz_ratio:.1f}% menor]")

            count += 1

    total_gz_ratio = (100 - (total_gz / total_orig * 100)) if total_orig > 0 else 0.0
    summary = (
        f"[+] Compressão concluída: {count} arquivo(s), "
        f"{total_orig:,} B -> Gzip {total_gz:,} B ({total_gz_ratio:.1f}% menor)"
    )
    if has_brotli:
        total_br_ratio = (100 - (total_br / total_orig * 100)) if total_orig > 0 else 0.0
        summary += f" | Brotli {total_br:,} B ({total_br_ratio:.1f}% menor)"
    print(f"{summary}.")


def mostrar_status() -> None:
    print("=" * 60)
    print("STATUS DO PROJETO TARGETS_VIRTU")
    print("=" * 60)
    print(f"Base: {BASE_DIR}\n")

    def listar(pasta: Path, titulo: str) -> None:
        print(f"[{titulo}] - {pasta}:")
        if not pasta.exists():
            print("  (diretório não existe)")
            return
        arquivos = [i for i in pasta.rglob("*") if i.is_file()]
        if not arquivos:
            print("  (vazio)")
        else:
            for a in sorted(arquivos):
                rel = a.relative_to(pasta)
                size = a.stat().st_size
                print(f"  • {rel} ({size:,} bytes)")
        print()

    listar(REGRAS_DIR, "Regras / Documentação")
    listar(EDIT_SRC, "Edit (Código-fonte ativo)")
    listar(LIB_DIR, "Lib (Bibliotecas / Módulos Compartilhados)")
    listar(TEST_DIR, "Test (Testes Automatizados)")
    listar(BACKUP_SRC, "Backup (Última versão preservada)")
    listar(DATA_DIR, "Data (Versão comprimida .gz/.br para produção)")
    listar(ANDROID_WWW, "Android (Capacitor www)")


def main() -> None:
    if len(sys.argv) < 2:
        print("Uso: python3 gerenciador_projeto.py [backup | comprimir | android | all | status]")
        sys.exit(1)

    cmd = sys.argv[1].lower()
    if cmd == "backup":
        fazer_backup()
    elif cmd in ("comprimir", "compress", "gz", "br"):
        comprimir_gz()
    elif cmd in ("android", "www"):
        sincronizar_android()
    elif cmd in ("all", "sync"):
        fazer_backup()
        comprimir_gz()
        sincronizar_android()
    elif cmd == "status":
        mostrar_status()
    else:
        print(f"Comando desconhecido: {cmd}")
        print("Opções válidas: backup, comprimir, android, all, status")
        sys.exit(1)


if __name__ == "__main__":
    main()
