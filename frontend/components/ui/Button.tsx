import type { ButtonHTMLAttributes } from 'react';

type Variant = 'primary' | 'secondary' | 'danger' | 'ghost';
type Size = 'sm' | 'md' | 'lg';

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
}

const SIZE_CLASS: Record<Size, string> = { sm: ' btn-sm', md: '', lg: ' btn-lg' };

export function Button({
  variant = 'secondary',
  size = 'md',
  className = '',
  type = 'button',
  ...rest
}: ButtonProps) {
  const cls = `btn-${variant}${SIZE_CLASS[size]}${className ? ` ${className}` : ''}`;
  return <button type={type} className={cls} {...rest} />;
}
