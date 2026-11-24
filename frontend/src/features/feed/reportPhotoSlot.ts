import type { ComponentType } from 'react'
import { ReportPhotoSheet as ReportPhotoSheetF16 } from '../media/ReportPhotoSheet'

/** What the post menu hands the report sheet: the photo showing in the carousel. */
export type ReportPhotoSheetProps = {
  photoId: string
  deedId: string
  onClose: () => void
}

/**
 * The "Report photo" sheet (F16, `src/features/media/ReportPhotoSheet`). When this is null, the
 * post menu leaves the "Report photo" row out.
 */
export const ReportPhotoSheet: ComponentType<ReportPhotoSheetProps> | null = ReportPhotoSheetF16
