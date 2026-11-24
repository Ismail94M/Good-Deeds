import { BadgeShelf } from './BadgeShelf'
import { HeroTitles } from './HeroTitles'
import type { Badge, HeroTitle } from './scoringApi'

/** A person's badges as achievement medals (public: earned badges only, no animation). */
export function BadgeList({ badges }: { badges: Badge[] }) {
  return <BadgeShelf badges={badges} />
}

/** Kindness Hero titles as achievement cards (D75); see `HeroTitles`. */
export function TitlesList({ titles }: { titles: HeroTitle[] }) {
  if (titles.length === 0) return <p className="text-sm text-slate-600">No Hero titles yet.</p>
  return <HeroTitles titles={titles} />
}
