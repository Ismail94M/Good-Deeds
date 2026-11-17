import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { describe, expect, it, vi } from 'vitest'
import { CategoryPills } from './CategoryPills'
import { DescriptionField } from './DescriptionField'
import {
  descriptionError,
  descriptionHint,
  fromBeneficiariesCount,
  PEOPLE_DEFAULT,
  toBeneficiariesCount,
} from './formValues'
import { PeopleStepper } from './PeopleStepper'

const CATEGORIES = [
  { slug: 'helping_elderly', label: 'Helping an elderly person' },
  { slug: 'animals', label: 'Animals' },
  { slug: 'other', label: 'Other' },
]

describe('CategoryPills', () => {
  function Harness({ initial = null }: { initial?: string | null }) {
    const [value, setValue] = useState<string | null>(initial)
    return <CategoryPills categories={CATEGORIES} value={value} onChange={setValue} />
  }

  it('renders one toggle button per category under the legend, none selected by default', () => {
    render(<Harness />)
    const group = screen.getByRole('group', { name: 'What kind of deed?' })
    const pills = within(group).getAllByRole('button')
    expect(pills.map((pill) => pill.textContent)).toEqual([
      'Helping an elderly person',
      'Animals',
      'Other',
    ])
    for (const pill of pills) expect(pill).toHaveAttribute('aria-pressed', 'false')
  })

  it('selects exactly one pill at a time', async () => {
    const user = userEvent.setup()
    render(<Harness />)
    await user.click(screen.getByRole('button', { name: 'Animals' }))
    expect(screen.getByRole('button', { name: 'Animals' })).toHaveAttribute('aria-pressed', 'true')

    await user.click(screen.getByRole('button', { name: 'Other' }))
    expect(screen.getByRole('button', { name: 'Other' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('button', { name: 'Animals' })).toHaveAttribute('aria-pressed', 'false')
  })

  it('reports the slug, takes a custom legend and shows an error', () => {
    const onChange = vi.fn()
    render(
      <CategoryPills
        categories={CATEGORIES}
        value="animals"
        onChange={onChange}
        legend="Category"
        error="Choose what kind of deed it was."
      />,
    )
    screen.getByRole('button', { name: 'Other' }).click()
    expect(onChange).toHaveBeenCalledWith('other')
    const group = screen.getByRole('group', { name: 'Category' })
    expect(group).toHaveAccessibleDescription(/choose what kind of deed it was/i)
  })
})

describe('DescriptionField', () => {
  function Harness({ error }: { error?: string | null }) {
    const [value, setValue] = useState('')
    return (
      <DescriptionField
        value={value}
        onChange={setValue}
        max={20}
        hint={descriptionHint(10)}
        error={error}
      />
    )
  }

  it('labels the textarea, shows the hint and counts characters', async () => {
    const user = userEvent.setup()
    render(<Harness />)
    const box = screen.getByLabelText('What did they do?')
    expect(box).toHaveAttribute('rows', '3')
    expect(screen.getByText('0 / 20')).toBeInTheDocument()
    expect(box).toHaveAccessibleDescription(
      'At least 10 characters. Please leave out phone numbers and addresses. 0 of 20 characters',
    )

    await user.type(box, 'Fed a dog')
    expect(screen.getByText('9 / 20')).toBeInTheDocument()
  })

  it('stops at the maximum length', async () => {
    const user = userEvent.setup()
    render(<Harness />)
    await user.type(screen.getByLabelText('What did they do?'), 'x'.repeat(25))
    expect(screen.getByText('20 / 20')).toBeInTheDocument()
  })

  it('marks the field invalid with the message', () => {
    render(<Harness error={descriptionError('short', 10)} />)
    const box = screen.getByLabelText('What did they do?')
    expect(box).toHaveAttribute('aria-invalid', 'true')
    expect(box).toHaveAccessibleDescription(
      /write at least 10 characters so people understand what happened/i,
    )
  })

  it('takes another label without a hint (self-log form)', () => {
    render(<DescriptionField label="What did you do?" value="abc" onChange={() => {}} max={1000} />)
    const box = screen.getByLabelText('What did you do?')
    expect(box).not.toHaveAttribute('aria-invalid')
    expect(box).toHaveAccessibleDescription('3 of 1000 characters')
  })

  it('validates the trimmed length', () => {
    expect(descriptionError('          ', 10)).toMatch(/write at least 10 characters/i)
    expect(descriptionError('  123456789  ', 10)).not.toBeNull()
    expect(descriptionError('1234567890', 10)).toBeNull()
  })
})

describe('PeopleStepper', () => {
  function Harness({ initial = PEOPLE_DEFAULT, max = 3 }: { initial?: number; max?: number }) {
    const [value, setValue] = useState(initial)
    return <PeopleStepper value={value} onChange={setValue} max={max} />
  }

  it('starts at 1 and steps between 0 and the maximum', async () => {
    const user = userEvent.setup()
    render(<Harness />)
    const group = screen.getByRole('group', { name: /how many people did they help\?/i })
    const value = within(group).getByRole('status')
    expect(value).toHaveTextContent('1')

    const fewer = within(group).getByRole('button', { name: 'One fewer' })
    const more = within(group).getByRole('button', { name: 'One more' })
    await user.click(fewer)
    expect(value).toHaveTextContent('0')
    expect(fewer).toBeDisabled()

    await user.click(more)
    await user.click(more)
    await user.click(more)
    expect(value).toHaveTextContent('3')
    expect(more).toBeDisabled()
    expect(fewer).toBeEnabled()
  })

  it('says the question is optional', () => {
    render(<Harness />)
    expect(screen.getByText('(optional)')).toBeInTheDocument()
  })

  it('sends 0 as null and shows a stored null as 0', () => {
    expect(toBeneficiariesCount(0)).toBeNull()
    expect(toBeneficiariesCount(2)).toBe(2)
    expect(fromBeneficiariesCount(null)).toBe(0)
    expect(fromBeneficiariesCount(4)).toBe(4)
  })
})
