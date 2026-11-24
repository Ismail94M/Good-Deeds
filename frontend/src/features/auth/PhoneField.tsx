import type { ComponentProps } from 'react'
import { TextField } from '../../components/ui'

type Props = Omit<
  ComponentProps<typeof TextField>,
  'label' | 'type' | 'inputMode' | 'autoComplete' | 'placeholder' | 'hint'
> & { label?: string }

/** Indian mobile number input, shared by phone verification and forgot password. */
export function PhoneField({ label = 'Indian mobile number', name = 'phone', ...props }: Props) {
  return (
    <TextField
      label={label}
      name={name}
      type="tel"
      inputMode="tel"
      autoComplete="tel"
      placeholder="+91 98765 43210"
      hint="10 digits. You can leave out +91. Only Indian numbers work for now."
      {...props}
    />
  )
}
