import type { ShareTarget } from './feedApi'
import { canWebShare, copyText } from './shareHelpers'
import { SHARE_TEXT, SHARE_TITLE, whatsAppHref } from './feedText'
import {
  DotsIcon,
  LinkIcon,
  Sheet,
  SheetButton,
  SheetCancel,
  SheetLink,
  SheetRowIcon,
  WhatsAppIcon,
} from './feedUi'

/**
 * "Share this deed" (F7 share sheet): WhatsApp, Copy link and, where the browser supports it,
 * More options (Web Share API). The text is generic (Q40), so no name or area travels with the
 * link. Each choice reports `onShared(target)` so the page can record it.
 */
export function ShareSheet({
  url,
  subtitle,
  onShared,
  onCopied,
  onClose,
}: {
  url: string
  /** "{doer} · {category}". */
  subtitle: string
  onShared: (target: ShareTarget) => void
  onCopied: (ok: boolean) => void
  onClose: () => void
}) {
  const copy = async () => {
    const ok = await copyText(url)
    if (ok) onShared('copy_link')
    onCopied(ok)
    onClose()
  }

  const webShare = async () => {
    try {
      await navigator.share({ title: SHARE_TITLE, text: SHARE_TEXT, url })
      onShared('web_share')
    } catch {
      // Cancelled by the person, or the OS refused: nothing to record.
    }
    onClose()
  }

  return (
    <Sheet title="Share this deed" subtitle={subtitle} onClose={onClose}>
      <div className="flex flex-col gap-1">
        <SheetLink
          href={whatsAppHref(url)}
          icon={
            <SheetRowIcon tone="bg-green-100 text-green-800">
              <WhatsAppIcon />
            </SheetRowIcon>
          }
          onClick={() => {
            onShared('whatsapp')
            onClose()
          }}
        >
          WhatsApp
        </SheetLink>
        <SheetButton
          icon={
            <SheetRowIcon tone="bg-brand-50 text-brand-900">
              <LinkIcon />
            </SheetRowIcon>
          }
          onClick={() => void copy()}
        >
          Copy link
        </SheetButton>
        {canWebShare() && (
          <SheetButton
            icon={
              <SheetRowIcon tone="bg-slate-100 text-slate-700">
                <DotsIcon />
              </SheetRowIcon>
            }
            onClick={() => void webShare()}
          >
            More options (Instagram and other apps)
          </SheetButton>
        )}
      </div>
      <p className="rounded-[10px] bg-slate-50 px-3 py-2.5 text-sm text-slate-600">
        Anyone with the link can see this one deed, but not your profile or other deeds. If the deed
        is hidden, the link stops working.
      </p>
      <SheetCancel onClick={onClose} />
    </Sheet>
  )
}
