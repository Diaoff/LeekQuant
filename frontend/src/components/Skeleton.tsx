import React from 'react'

interface SkeletonProps {
  className?: string
  style?: React.CSSProperties
}

function ShimmerBar({ className = '', style }: { className?: string; style?: React.CSSProperties }) {
  return (
    <div
      className={`skeleton-shimmer rounded ${className}`}
      style={style}
      aria-hidden="true"
    />
  )
}

function SkeletonLine({ className = '', style }: SkeletonProps) {
  return <ShimmerBar className={`h-4 w-full ${className}`} style={style} />
}

function SkeletonTable({ rows = 5, columns = 4 }: { rows?: number; columns?: number }) {
  return (
    <div className="space-y-3" role="status" aria-label="Loading table">
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="flex gap-4">
          {Array.from({ length: columns }).map((_, j) => (
            <ShimmerBar
              key={j}
              className="h-5 flex-1 rounded"
            />
          ))}
        </div>
      ))}
    </div>
  )
}

function SkeletonCard({ lines = 3 }: { lines?: number }) {
  return (
    <div className="rounded-lg border border-line p-4" role="status" aria-label="Loading card">
      <ShimmerBar className="mb-4 h-6 w-1/3" />
      <div className="space-y-3">
        {Array.from({ length: lines }).map((_, i) => (
          <ShimmerBar
            key={i}
            className={`h-4 ${i === lines - 1 ? 'w-2/3' : 'w-full'}`}
          />
        ))}
      </div>
    </div>
  )
}

const Skeleton = {
  Line: SkeletonLine,
  Table: SkeletonTable,
  Card: SkeletonCard,
}

export default Skeleton
