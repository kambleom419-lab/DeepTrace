import { useCallback, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { FileVideo, Loader2, ScanSearch, UploadCloud, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { api } from '@/lib/api'
import { formatBytes } from '@/lib/utils'

const ACCEPTED = ['video/mp4', 'video/quicktime', 'video/x-msvideo']
const MAX_SIZE = 500 * 1024 * 1024 // 500 MB

export default function Upload() {
  const navigate = useNavigate()
  const inputRef = useRef<HTMLInputElement>(null)
  const [file, setFile] = useState<File | null>(null)
  const [dragging, setDragging] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const selectFile = useCallback((f: File) => {
    setError(null)
    if (!ACCEPTED.includes(f.type) && !f.name.match(/\.(mp4|mov|avi)$/i)) {
      setError('Unsupported format. Accepted: MP4, MOV, AVI.')
      return
    }
    if (f.size > MAX_SIZE) {
      setError(`File too large (max ${formatBytes(MAX_SIZE)}).`)
      return
    }
    setFile(f)
  }, [])

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault()
    setDragging(false)
    const f = e.dataTransfer.files?.[0]
    if (f) selectFile(f)
  }

  const handleUpload = async () => {
    if (!file) return
    setUploading(true)
    setError(null)
    try {
      const inv = await api.uploadInvestigation(file, file.name)
      navigate(`/processing/${inv.id}`)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Upload failed')
      setUploading(false)
    }
  }

  return (
    <div className="mx-auto max-w-2xl space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight text-ink">New Analysis</h1>
        <p className="mt-1 text-sm text-ink-dim">
          Submit a video for deepfake forensic analysis
        </p>
      </div>

      <Card className="border-edge-2">
        <CardContent className="p-6">
          {!file ? (
            <div
              role="button"
              tabIndex={0}
              onClick={() => inputRef.current?.click()}
              onKeyDown={(e) => e.key === 'Enter' && inputRef.current?.click()}
              onDragOver={(e) => {
                e.preventDefault()
                setDragging(true)
              }}
              onDragLeave={() => setDragging(false)}
              onDrop={handleDrop}
              className={`flex cursor-pointer flex-col items-center justify-center rounded-lg border-2 border-dashed px-6 py-16 text-center transition-colors ${
                dragging
                  ? 'border-neon bg-neon/5'
                  : 'border-edge-2 bg-bg/40 hover:border-neon/50'
              }`}
            >
              <UploadCloud
                className={`mb-4 h-10 w-10 ${dragging ? 'text-neon' : 'text-ink-faint'}`}
              />
              <p className="font-mono text-sm uppercase tracking-widest text-ink">
                Drop video here
              </p>
              <p className="mt-1 text-xs text-ink-faint">
                or click to browse · MP4 / MOV / AVI · max {formatBytes(MAX_SIZE)}
              </p>
              <input
                ref={inputRef}
                type="file"
                accept=".mp4,.mov,.avi"
                className="hidden"
                onChange={(e) => {
                  const f = e.target.files?.[0]
                  if (f) selectFile(f)
                  e.target.value = ''
                }}
              />
            </div>
          ) : (
            <div className="space-y-4">
              <div className="flex items-start justify-between gap-4 rounded-lg border border-edge bg-bg/40 p-4">
                <div className="flex min-w-0 items-center gap-3">
                  <FileVideo className="h-8 w-8 shrink-0 text-neon" />
                  <div className="min-w-0">
                    <p className="truncate font-mono text-sm text-ink">{file.name}</p>
                    <p className="mt-0.5 font-mono text-[0.7rem] text-ink-faint">
                      {formatBytes(file.size)} · {file.type || 'video'}
                    </p>
                  </div>
                </div>
                <Button
                  variant="ghost"
                  size="icon"
                  onClick={() => setFile(null)}
                  title="Remove file"
                  disabled={uploading}
                >
                  <X className="h-4 w-4" />
                </Button>
              </div>

              <div className="rounded-lg border border-edge bg-panel-2/60 p-4 font-mono text-[0.7rem] leading-relaxed text-ink-dim">
                <p className="text-neon">ANALYSIS QUEUE</p>
                <p>1. Ingest &amp; hash (SHA-256)</p>
                <p>2. Frame extraction (FFmpeg)</p>
                <p>3. Face detection &amp; alignment</p>
                <p>4. Spatial / Temporal / Frequency branches</p>
                <p>5. Feature fusion → verdict + heatmaps</p>
              </div>

              {error && <p className="font-mono text-xs text-alert">UPLOAD_ERROR :: {error}</p>}

              <div className="flex justify-end gap-3">
                <Button variant="ghost" onClick={() => setFile(null)} disabled={uploading}>
                  Cancel
                </Button>
                <Button onClick={handleUpload} disabled={uploading || !file}>
                  {uploading ? (
                    <>
                      <Loader2 className="animate-spin" />
                      Uploading…
                    </>
                  ) : (
                    <>
                      <ScanSearch />
                      Start analysis
                    </>
                  )}
                </Button>
              </div>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
