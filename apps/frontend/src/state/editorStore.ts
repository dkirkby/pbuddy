import { create } from 'zustand'
import type { BallColorModel, CourtGeometry, StableBounds } from '../types/api'

interface EditorState {
  stableBounds: StableBounds | null
  courtGeometry: CourtGeometry | null
  ballColorModel: BallColorModel | null
  isDirty: boolean

  setStableBounds: (v: StableBounds) => void
  setCourtGeometry: (v: CourtGeometry) => void
  setBallColorModel: (v: BallColorModel) => void
  initFromRaw: (bounds: StableBounds, court: CourtGeometry, ball: BallColorModel) => void
  markClean: () => void
  reset: () => void
}

export const useEditorStore = create<EditorState>((set) => ({
  stableBounds: null,
  courtGeometry: null,
  ballColorModel: null,
  isDirty: false,

  setStableBounds: (v) => set({ stableBounds: v, isDirty: true }),
  setCourtGeometry: (v) => set({ courtGeometry: v, isDirty: true }),
  setBallColorModel: (v) => set({ ballColorModel: v, isDirty: true }),
  initFromRaw: (bounds, court, ball) =>
    set({ stableBounds: bounds, courtGeometry: court, ballColorModel: ball, isDirty: false }),
  markClean: () => set({ isDirty: false }),
  reset: () => set({ stableBounds: null, courtGeometry: null, ballColorModel: null, isDirty: false }),
}))
