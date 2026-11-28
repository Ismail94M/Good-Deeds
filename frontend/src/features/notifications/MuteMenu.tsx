import { firstName } from '../report/format'
import { Sheet, SheetButton, SheetCancel, SheetRowIcon } from '../feed/feedUi'
import type { NotificationItem } from './notificationsApi'

function Icon({ d }: { d: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      className="size-5"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d={d} />
    </svg>
  )
}

const READ_ICON = 'M5 12.5l4.5 4.5L19 7.5'
const UNREAD_ICON = 'M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8z'
const MUTE_ICON =
  'M6 9.5a6 6 0 0 1 9.6-4.8M18 9.5c0 4.6 1.8 6.4 2.5 7H8M3 3l18 18M10 20a2.2 2.2 0 0 0 4 0'

/** The name of the person a row can mute, from its actors. */
export function muteName(item: NotificationItem): string | null {
  const handle = item.can_mute.person
  if (!handle) return null
  const actor = item.actors.find((a) => a.handle === handle)
  return actor ? firstName(actor.display_name) : `@${handle}`
}

/**
 * A row's "⋯" sheet (§6.3, D113): Mark as read/unread, Mute {name} and Turn off notifications
 * for this deed. Mutes are silent; the other person is never told.
 */
export function MuteMenu({
  item,
  onClose,
  onToggleRead,
  onMutePerson,
  onMuteDeed,
}: {
  item: NotificationItem
  onClose: () => void
  onToggleRead: () => void
  onMutePerson: (handle: string) => void
  onMuteDeed: (deedId: string) => void
}) {
  const name = muteName(item)
  const { person, deed } = item.can_mute
  return (
    <Sheet title="Notification options" onClose={onClose}>
      <SheetButton
        icon={
          <SheetRowIcon tone="bg-brand-50 text-brand-700">
            <Icon d={item.read ? UNREAD_ICON : READ_ICON} />
          </SheetRowIcon>
        }
        onClick={onToggleRead}
      >
        {item.read ? 'Mark as unread' : 'Mark as read'}
      </SheetButton>
      {person && name && (
        <SheetButton
          icon={
            <SheetRowIcon tone="bg-slate-100 text-slate-700">
              <Icon d={MUTE_ICON} />
            </SheetRowIcon>
          }
          onClick={() => onMutePerson(person)}
        >
          Mute {name}
        </SheetButton>
      )}
      {deed && (
        <SheetButton
          icon={
            <SheetRowIcon tone="bg-slate-100 text-slate-700">
              <Icon d={MUTE_ICON} />
            </SheetRowIcon>
          }
          onClick={() => onMuteDeed(deed)}
        >
          Turn off notifications for this deed
        </SheetButton>
      )}
      <SheetCancel onClick={onClose} />
    </Sheet>
  )
}
