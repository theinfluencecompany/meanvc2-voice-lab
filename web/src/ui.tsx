import type { ButtonHTMLAttributes, InputHTMLAttributes, HTMLAttributes } from "react";
type Variant = "default" | "secondary" | "outline";
export function Button({ variant = "default", className = "", ...props }: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant }) { return <button type="button" className={`ui-button ui-button-${variant} ${className}`} {...props} />; }
export function Badge({ variant = "secondary", className = "", ...props }: HTMLAttributes<HTMLSpanElement> & { variant?: Variant }) { return <span className={`ui-badge ui-badge-${variant} ${className}`} {...props} />; }
export function Input({ className = "", ...props }: InputHTMLAttributes<HTMLInputElement>) { return <input className={`ui-input ${className}`} {...props} />; }
