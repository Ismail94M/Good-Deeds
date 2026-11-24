import { FlagIcon, LinkIcon, Sheet, SheetButton, SheetCancel, SheetRowIcon } from './feedUi'

/**
 * The card's "⋯" menu (photos canvas): Report photo (only when the card shows a photo and I
 * didn't upload it) and Copy link (only while the deed has a share link).
 */
export function PostMenuSheet({
  doerName,
  onReportPhoto,
  onCopyLink,
  onClose,
}: {
  doerName: string
  onReportPhoto: (() => void) | null
  onCopyLink: (() => void) | null
  onClose: () => void
}) {
  return (
    <Sheet title={`${doerName}’s deed`} onClose={onClose}>
      <div className="flex flex-col gap-1">
        {onReportPhoto && (
          <SheetButton
            icon={
              <SheetRowIcon tone="bg-red-50 text-red-700">
                <FlagIcon />
              </SheetRowIcon>
            }
            onClick={onReportPhoto}
          >
            Report photo
          </SheetButton>
        )}
        {onCopyLink && (
          <SheetButton
            icon={
              <SheetRowIcon tone="bg-brand-50 text-brand-900">
                <LinkIcon />
              </SheetRowIcon>
            }
            onClick={onCopyLink}
          >
            Copy link
          </SheetButton>
        )}
      </div>
      <SheetCancel onClick={onClose} />
    </Sheet>
  )
}
