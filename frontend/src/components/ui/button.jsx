import * as React from 'react';
import { Slot } from '@radix-ui/react-slot';
import { cva } from 'class-variance-authority';
import { cn } from '@/lib/utils';

const buttonVariants = cva(
  'inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-[10px] text-sm font-semibold ' +
  'ring-offset-background transition-all focus-visible:outline-none focus-visible:ring-2 ' +
  'focus-visible:ring-ring focus-visible:ring-offset-2 disabled:pointer-events-none disabled:opacity-50 ' +
  '[&_svg]:pointer-events-none [&_svg]:size-4 [&_svg]:shrink-0 cursor-pointer',
  {
    variants: {
      variant: {
        default:     'text-white hover:-translate-y-0.5',
        destructive: 'bg-destructive text-destructive-foreground hover:bg-destructive/90',
        outline:     'border border-input bg-background hover:border-[var(--neon)] hover:text-[var(--neon)]',
        secondary:   'bg-secondary text-secondary-foreground hover:bg-secondary/80',
        ghost:       'hover:bg-accent hover:text-accent-foreground',
        link:        'text-primary underline-offset-4 hover:underline',
      },
      size: {
        default: 'h-10 px-4 py-2',
        sm:      'h-9 rounded-[10px] px-3',
        lg:      'h-11 rounded-[10px] px-8',
        icon:    'h-10 w-10',
        xs:      'h-7 rounded-lg px-2 text-xs',
      },
    },
    defaultVariants: {
      variant: 'default',
      size:    'default',
    },
  }
);

const Button = React.forwardRef(({ className, variant, size, asChild = false, style, ...props }, ref) => {
  const Comp = asChild ? Slot : 'button';
  const isDefault = !variant || variant === 'default';
  return (
    <Comp
      className={cn(buttonVariants({ variant, size, className }))}
      ref={ref}
      style={isDefault ? {
        background: 'linear-gradient(135deg, var(--neon), var(--accent-blue))',
        boxShadow: '0 4px 20px rgba(0,200,255,.25)',
        ...style,
      } : style}
      {...props}
    />
  );
});
Button.displayName = 'Button';

export { Button, buttonVariants };
