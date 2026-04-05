'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';

const links = [
  { href: '/creators', label: 'Creators' },
  { href: '/live',     label: 'Live'     },
];

export default function Nav() {
  const path = usePathname();

  return (
    <header className="flex items-center gap-6 border-b border-gray-800 bg-gray-900 px-6 py-3">
      <span className="text-lg font-bold tracking-tight text-brand-500">
        LexxiLive
      </span>
      <nav className="flex gap-1">
        {links.map(({ href, label }) => (
          <Link
            key={href}
            href={href}
            className={`rounded-md px-3 py-1.5 text-sm font-medium transition-colors ${
              path.startsWith(href)
                ? 'bg-brand-600 text-white'
                : 'text-gray-400 hover:bg-gray-800 hover:text-gray-100'
            }`}
          >
            {label}
          </Link>
        ))}
      </nav>
    </header>
  );
}
