# ----------------------------------------------------------------------------
#  Test en machine virtuelle (QEMU/KVM)
# ----------------------------------------------------------------------------
test_vm() {
  local fw=uefi iso=""
  while (($#)); do case $1 in --bios) fw=bios ;; *) iso=$1 ;; esac; shift; done
  [[ -f $iso ]] || die "usage : $0 --test-vm [--bios] fichier.iso"
  need qemu-system-x86_64 && need qemu-img || die "installez qemu-system-x86 (et ovmf pour l'UEFI)"
  mkdir -p "$WORKDIR"
  local disk="$WORKDIR/test-vm-$fw.qcow2" accel=(-accel tcg) cpu=max fwargs=() code="" c
  [[ -f $disk ]] || qemu-img create -q -f qcow2 "$disk" 64G
  if [[ -w /dev/kvm ]]; then accel=(-enable-kvm); cpu=host; else warn "KVM indisponible : la VM sera très lente"; fi
  if [[ $fw == uefi ]]; then
    for c in /usr/share/OVMF/OVMF_CODE_4M.fd /usr/share/OVMF/OVMF_CODE.fd /usr/share/edk2/ovmf/OVMF_CODE.fd /usr/share/edk2/x64/OVMF_CODE.4m.fd; do
      [[ -f $c ]] && { code=$c; break; }
    done
    [[ -n $code ]] || die "firmware OVMF introuvable (paquet ovmf)"
    [[ -f $WORKDIR/test-vm-vars.fd ]] || cp "${code/CODE/VARS}" "$WORKDIR/test-vm-vars.fd"
    fwargs=(-drive "if=pflash,format=raw,readonly=on,file=$code" -drive "if=pflash,format=raw,file=$WORKDIR/test-vm-vars.fd")
  fi
  log "VM de test ($fw) : console web sur https://localhost:8443/, SSH sur le port 2222"
  qemu-system-x86_64 -name SeedBoxBuntu-test -machine q35 "${accel[@]}" -cpu "$cpu" -smp 4 -m 8192 "${fwargs[@]}" \
    -drive "id=hd,if=none,file=$disk,format=qcow2" -device virtio-blk-pci,drive=hd,bootindex=1 \
    -drive "id=cd,if=none,media=cdrom,readonly=on,file=$iso" -device ide-cd,drive=cd,bootindex=2 \
    -netdev user,id=n0,hostfwd=tcp::8443-:443,hostfwd=tcp::2222-:22 -device virtio-net-pci,netdev=n0 \
    -vga std
}
