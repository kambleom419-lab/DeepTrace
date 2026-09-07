import { TooltipProvider } from '@/components/ui/tooltip'
import { AppRouter } from '@/app/router'

export function App() {
  return (
    <TooltipProvider delayDuration={200}>
      <div className="scanlines min-h-screen bg-bg">
        <AppRouter />
      </div>
    </TooltipProvider>
  )
}
