type IconProps = { className?: string };

export function GridIcon({ className }: IconProps) {
  return (
    <svg width="15" height="15" viewBox="0 0 16 16" fill="none" className={className}>
      <rect x="1.8" y="1.8" width="5.2" height="5.2" rx="1.4" stroke="currentColor" strokeWidth="1.3" />
      <rect x="9" y="1.8" width="5.2" height="5.2" rx="1.4" stroke="currentColor" strokeWidth="1.3" />
      <rect x="1.8" y="9" width="5.2" height="5.2" rx="1.4" stroke="currentColor" strokeWidth="1.3" />
      <rect x="9" y="9" width="5.2" height="5.2" rx="1.4" stroke="currentColor" strokeWidth="1.3" />
    </svg>
  );
}

export function ListIcon({ className }: IconProps) {
  return (
    <svg width="15" height="15" viewBox="0 0 16 16" fill="none" className={className}>
      <path d="M2 4h12M2 8h8M2 12h10" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
    </svg>
  );
}

export function CheckIcon({ className }: IconProps) {
  return (
    <svg width="15" height="15" viewBox="0 0 16 16" fill="none" className={className}>
      <path d="M3.2 8.4l2.9 2.9 6.7-6.7" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export function DocIcon({ className }: IconProps) {
  return (
    <svg width="15" height="15" viewBox="0 0 16 16" fill="none" className={className}>
      <path d="M4 1.8h5.5L12 4.3v9.9a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V2.8a1 1 0 0 1 1-1Z" stroke="currentColor" strokeWidth="1.2" strokeLinejoin="round" />
      <path d="M5 7h6M5 9.6h6M5 12.2h3.5" stroke="currentColor" strokeWidth="1.1" strokeLinecap="round" />
    </svg>
  );
}

export function CardIcon({ className }: IconProps) {
  return (
    <svg width="15" height="15" viewBox="0 0 16 16" fill="none" className={className}>
      <rect x="2.2" y="3" width="11.6" height="10" rx="1.6" stroke="currentColor" strokeWidth="1.3" />
      <path d="M5 6.4h6M5 9.2h3.6" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
    </svg>
  );
}

export function BookIcon({ className }: IconProps) {
  return (
    <svg width="15" height="15" viewBox="0 0 16 16" fill="none" className={className}>
      <path d="M2.5 3.2c1.4-.7 3-.7 4.5 0v9.6c-1.5-.7-3.1-.7-4.5 0V3.2Z" stroke="currentColor" strokeWidth="1.2" strokeLinejoin="round" />
      <path d="M13.5 3.2c-1.4-.7-3-.7-4.5 0v9.6c1.5-.7 3.1-.7 4.5 0V3.2Z" stroke="currentColor" strokeWidth="1.2" strokeLinejoin="round" />
    </svg>
  );
}

export function GearIcon({ className }: IconProps) {
  return (
    <svg width="15" height="15" viewBox="0 0 16 16" fill="none" className={className}>
      <path
        d="M2.5 4.5h11M2.5 8h11M2.5 11.5h11"
        stroke="currentColor"
        strokeWidth="1.3"
        strokeLinecap="round"
      />
      <circle cx="5.6" cy="4.5" r="1.6" fill="var(--bg-sidebar)" stroke="currentColor" strokeWidth="1.3" />
      <circle cx="10.4" cy="11.5" r="1.6" fill="var(--bg-sidebar)" stroke="currentColor" strokeWidth="1.3" />
    </svg>
  );
}

export function PanelCollapseIcon({ className }: IconProps) {
  return (
    <svg width="15" height="15" viewBox="0 0 16 16" fill="none" className={className}>
      <rect x="1.8" y="2.4" width="12.4" height="11.2" rx="1.8" stroke="currentColor" strokeWidth="1.2" />
      <path d="M6.2 2.4v11.2" stroke="currentColor" strokeWidth="1.2" />
      <path d="M4.2 6.4l-1.4 1.6 1.4 1.6" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export function PanelExpandIcon({ className }: IconProps) {
  return (
    <svg width="15" height="15" viewBox="0 0 16 16" fill="none" className={className}>
      <rect x="1.8" y="2.4" width="12.4" height="11.2" rx="1.8" stroke="currentColor" strokeWidth="1.2" />
      <path d="M6.2 2.4v11.2" stroke="currentColor" strokeWidth="1.2" />
      <path d="M3 6.4l1.4 1.6-1.4 1.6" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export function LogoutIcon({ className }: IconProps) {
  return (
    <svg width="15" height="15" viewBox="0 0 16 16" fill="none" className={className}>
      <path d="M6.4 2H3.4a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1h3" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M10.2 11.2 13.4 8l-3.2-3.2M13.4 8H6" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export function MenuIcon({ className }: IconProps) {
  return (
    <svg width="17" height="17" viewBox="0 0 16 16" fill="none" className={className}>
      <path d="M2.2 4h11.6M2.2 8h11.6M2.2 12h11.6" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
    </svg>
  );
}

export function CloseIcon({ className }: IconProps) {
  return (
    <svg width="15" height="15" viewBox="0 0 16 16" fill="none" className={className}>
      <path d="M3.6 3.6l8.8 8.8M12.4 3.6l-8.8 8.8" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
    </svg>
  );
}
