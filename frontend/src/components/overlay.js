// Modal overlays are exclusive. A rejected controlled open must be reset by
// its caller before trying again; never queue it or dismiss an existing dialog.
export function showExclusiveDialog(element) {
  const current = document.querySelector('dialog:modal')
  if (current && current !== element) return false
  document.dispatchEvent(new Event('deepdocs:modal-opening'))
  element.showModal()
  return true
}
