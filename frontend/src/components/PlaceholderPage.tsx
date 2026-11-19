import { Page } from './ui'

/**
 * PLACEHOLDER (M3 F9): stands in for a screen a later M3 task builds (see the `task` prop and
 * docs/M3-DESIGN.md §7). Delete this component once no route uses it.
 */
export function PlaceholderPage({ title, task }: { title: string; task: string }) {
  return (
    <Page title={title}>
      <p role="note" data-placeholder={task} className="text-slate-600">
        This page is coming soon.
      </p>
    </Page>
  )
}
