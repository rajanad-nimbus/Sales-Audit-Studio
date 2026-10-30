'use client';

import { useId } from 'react';

/**
 * Sales Audit Studio Emblem:
 * Precision faceted audit shield with radiant gradient contour,
 * ledger transaction strata, dynamic verification vector (checkmark),
 * and luminous certification nodes.
 */
export function Logo({
  size = 20,
  title,
  className,
}: {
  size?: number;
  title?: string;
  className?: string;
}) {
  const rawId = useId();
  const id = rawId.replace(/[^a-zA-Z0-9]/g, '');

  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 32 32"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={className}
      role={title ? 'img' : undefined}
      aria-label={title}
      aria-hidden={title ? undefined : true}
    >
      <defs>
        <linearGradient
          id={`sas-p-${id}`}
          x1="4"
          y1="2"
          x2="28"
          y2="30"
          gradientUnits="userSpaceOnUse"
        >
          <stop offset="0%" stopColor="#38bdf8" />
          <stop offset="55%" stopColor="#3b82f6" />
          <stop offset="100%" stopColor="#1d4ed8" />
        </linearGradient>
        <linearGradient
          id={`sas-chk-${id}`}
          x1="8"
          y1="18"
          x2="25"
          y2="9"
          gradientUnits="userSpaceOnUse"
        >
          <stop offset="0%" stopColor="#38bdf8" />
          <stop offset="45%" stopColor="#22d3ee" />
          <stop offset="100%" stopColor="#34d399" />
        </linearGradient>
        <linearGradient
          id={`sas-fill-${id}`}
          x1="16"
          y1="2"
          x2="16"
          y2="30"
          gradientUnits="userSpaceOnUse"
        >
          <stop offset="0%" stopColor="#38bdf8" stopOpacity="0.12" />
          <stop offset="100%" stopColor="#1e3a8a" stopOpacity="0.28" />
        </linearGradient>
        <radialGradient id={`sas-glow-${id}`} cx="16" cy="16" r="11" gradientUnits="userSpaceOnUse">
          <stop offset="0%" stopColor="#38bdf8" stopOpacity="0.22" />
          <stop offset="100%" stopColor="#38bdf8" stopOpacity="0" />
        </radialGradient>
      </defs>
      <circle cx="16" cy="16" r="11" fill={`url(#sas-glow-${id})`} />
      <path
        d="M16 2.5L27.5 8.2V20.8L16 29.5L4.5 20.8V8.2L16 2.5Z"
        fill={`url(#sas-fill-${id})`}
        stroke={`url(#sas-p-${id})`}
        strokeWidth="1.8"
        strokeLinejoin="round"
      />
      <path
        d="M11 9.5H21"
        stroke="#93c5fd"
        strokeOpacity="0.75"
        strokeWidth="1.4"
        strokeLinecap="round"
      />
      <path
        d="M10 13.5H16.5"
        stroke="#93c5fd"
        strokeOpacity="0.5"
        strokeWidth="1.4"
        strokeLinecap="round"
      />
      <path
        d="M9.5 17.5L14 22L24 10"
        stroke={`url(#sas-chk-${id})`}
        strokeWidth="2.6"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <circle cx="16" cy="2.5" r="1.5" fill="#38bdf8" />
      <circle cx="27.5" cy="8.2" r="1.3" fill="#60a5fa" />
      <circle cx="24" cy="10" r="1.75" fill="#34d399" />
      <circle cx="16" cy="29.5" r="1.4" fill="#38bdf8" />
      <circle cx="4.5" cy="8.2" r="1.3" fill="#60a5fa" />
    </svg>
  );
}
