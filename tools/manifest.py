#!/usr/bin/env python3
"""Manifeste d'une version publiée : empreinte SHA-256 de chaque fichier de l'appliance.

    tools/manifest.py build --tag v1.2.0 --commit <sha> [--root .] > manifest.json
    tools/manifest.py sign manifest.json --key ~/.ssh/seedboxbuntu-release   -> manifest.json.sig
    tools/manifest.py verify manifest.json --signers allowed_signers          (contrôle local)

Le serveur calcule les mêmes empreintes sur l'archive téléchargée (appliance.tree_hashes) : tout fichier
ajouté, retiré ou modifié par rapport au manifeste signé fait refuser la mise à jour.
La signature est une signature SSH (ssh-keygen -Y sign), espace de noms « seedboxbuntu-release ».
La clé publique correspondante se déclare sur le serveur : Paramètres > Mise à jour de l'appliance.
"""
import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

HASHED_DIRS = ("rootfs", "stack", "templates", "p2p")
NAMESPACE = "seedboxbuntu-release"
PRINCIPAL = "seedboxbuntu"


def hashes(root):
    root = Path(root)
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for sub in HASHED_DIRS if (root / sub).is_dir()
            for path in sorted((root / sub).rglob("*")) if path.is_file() and "__pycache__" not in path.parts}


def build(args):
    data = {"version": 1, "tag": args.tag, "commit": args.commit, "files": hashes(args.root)}
    json.dump(data, sys.stdout, indent=1, sort_keys=True)
    sys.stdout.write("\n")


def sign(args):
    subprocess.run(["ssh-keygen", "-Y", "sign", "-f", args.key, "-n", NAMESPACE, args.manifest], check=True)


def verify(args):
    with open(args.manifest, "rb") as handle:
        result = subprocess.run(["ssh-keygen", "-Y", "verify", "-f", args.signers, "-I", PRINCIPAL, "-n", NAMESPACE,
                                 "-s", args.manifest + ".sig"], stdin=handle)
    sys.exit(result.returncode)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    b = commands.add_parser("build")
    b.add_argument("--tag", required=True)
    b.add_argument("--commit", required=True)
    b.add_argument("--root", default=".")
    s = commands.add_parser("sign")
    s.add_argument("manifest")
    s.add_argument("--key", required=True)
    v = commands.add_parser("verify")
    v.add_argument("manifest")
    v.add_argument("--signers", required=True)
    args = parser.parse_args()
    {"build": build, "sign": sign, "verify": verify}[args.command](args)


if __name__ == "__main__":
    main()
