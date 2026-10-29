import { createContext, useContext } from 'react'
import type { Catalog } from '../api/types'

export const CatalogContext = createContext<Catalog | null>(null)

export function useCatalog(): Catalog {
  const c = useContext(CatalogContext)
  if (!c) throw new Error('catalog not loaded')
  return c
}
