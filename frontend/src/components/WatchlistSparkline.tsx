import React from 'react'

interface SparklineProps {
  latestClose: number | null
  preClose: number | null
  tsCode: string
}

function getSparklinePoints(latestClose: number, preClose: number, tsCode: string): string {
  const numPoints = 20
  const svgWidth = 60
  const svgHeight = 20
  const padding = 2

  const seed = tsCode.split('').reduce((acc, ch) => acc + ch.charCodeAt(0), 0)
  const diff = Math.abs(latestClose - preClose)
  const minNoise = 0.003 * latestClose
  const amplitude = Math.max(diff, minNoise)

  const yValues: number[] = []
  for (let p = 0; p <= numPoints; p++) {
    const ratio = p / numPoints
    const base = preClose + (latestClose - preClose) * ratio
    const noise = (Math.sin(2.7 * p + seed) + Math.sin(1.3 * p + 0.3 * seed)) * amplitude * 0.25
    yValues.push(base + noise)
  }

  const yMin = Math.min(...yValues)
  const yMax = Math.max(...yValues)
  const yRange = yMax - yMin || 1

  return yValues
    .map((y, i) => {
      const x = (i / numPoints) * svgWidth
      const svgY = svgHeight - ((y - yMin) / yRange) * (svgHeight - padding * 2) - padding
      return x.toFixed(1) + ',' + svgY.toFixed(1)
    })
    .join(' ')
}

export default function WatchlistSparkline({ latestClose, preClose, tsCode }: SparklineProps) {
  const points = React.useMemo(() => {
    if (latestClose == null || preClose == null || preClose === 0) return '0,10 60,10'
    return getSparklinePoints(latestClose, preClose, tsCode)
  }, [latestClose, preClose, tsCode])

  const isUp = latestClose != null && preClose != null && latestClose >= preClose
  const strokeColor = isUp ? '#10b981' : '#ef4444'

  return (
    <svg
      viewBox="0 0 60 20"
      preserveAspectRatio="none"
      className="h-5 w-[60px]"
    >
      <polyline
        points={points}
        fill="none"
        stroke={strokeColor}
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  )
}
