import { create } from 'zustand'
import type { CourtGeometry, StableBounds } from '../types/api'

interface EditorState {
  stableBounds: StableBounds | null
  courtGeometry: CourtGeometry | null
  isDirty: boolean

  setStableBounds: (v: StableBounds) => void
  setCourtGeometry: (v: CourtGeometry) => void
  initFromRaw: (bounds: StableBounds, court: CourtGeometry) => void
  markClean: () => void
  reset: () => void
}

export const useEditorStore = create<EditorState>((set) => ({
  stableBounds: null,
  courtGeometry: null,
  isDirty: false,

  setStableBounds: (v) => set({ stableBounds: v, isDirty: true }),
  setCourtGeometry: (v) => set({ courtGeometry: v, isDirty: true }),
  initFromRaw: (bounds, court) =>
    set({ stableBounds: bounds, courtGeometry: court, isDirty: false }),
  markClean: () => set({ isDirty: false }),
  reset: () => set({ stableBounds: null, courtGeometry: null, isDirty: false }),
}))
