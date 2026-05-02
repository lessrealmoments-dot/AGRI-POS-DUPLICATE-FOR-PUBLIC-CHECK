/**
 * UnsavedChangesDialog — paired with useUnsavedChangesGuard.
 *
 * The page renders this once and the hook controls when it opens. Three
 * actions:
 *   • Stay  — cancel the navigation, return to editing.
 *   • Park & leave (Sales only)  — auto-saves the cart as a parked draft
 *     so the work is recoverable from the Parked dialog.
 *   • Leave & lose changes  — discard and proceed.
 */
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from './ui/dialog';
import { Button } from './ui/button';
import { AlertTriangle, PauseCircle, X, ArrowRight } from 'lucide-react';

export default function UnsavedChangesDialog({ guard }) {
  if (!guard) return null;
  return (
    <Dialog open={guard.isOpen} onOpenChange={(o) => { if (!o) guard.onCancel(); }}>
      <DialogContent className="max-w-md" data-testid="unsaved-changes-dialog">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2 text-amber-700">
            <AlertTriangle size={18} /> Unsaved changes
          </DialogTitle>
          <DialogDescription className="text-xs text-slate-500">
            You're about to leave <span className="font-semibold">{guard.label}</span> with unsaved work.
            What would you like to do?
          </DialogDescription>
        </DialogHeader>

        <div className="flex flex-col gap-2 pt-2">
          <Button
            onClick={guard.onCancel}
            data-testid="unsaved-stay"
            className="bg-[#1A4D2E] hover:bg-[#14532d] text-white justify-start"
          >
            <X size={14} className="mr-2" /> Stay on this page (keep my work)
          </Button>

          {guard.canPark && (
            <Button
              onClick={guard.onParkAndLeave}
              disabled={guard.parking}
              data-testid="unsaved-park"
              className="bg-amber-600 hover:bg-amber-700 text-white justify-start"
            >
              <PauseCircle size={14} className="mr-2" />
              {guard.parking ? 'Parking…' : 'Park sale & leave (recoverable later)'}
            </Button>
          )}

          <Button
            onClick={guard.onConfirmLeave}
            variant="ghost"
            data-testid="unsaved-leave"
            className="text-rose-600 hover:bg-rose-50 hover:text-rose-700 justify-start"
          >
            <ArrowRight size={14} className="mr-2" /> Leave & lose changes
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
