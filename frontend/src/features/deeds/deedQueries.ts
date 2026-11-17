import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { deedsApi, deedsKeys, type DeedOut } from './deedsApi'

/**
 * `GET /deeds/options` for labels and limits. Onboarded-only on the server, so screens open to
 * any signed-in user (My deeds) must cope with it being missing.
 */
export function useDeedOptions() {
  return useQuery({ queryKey: deedsKeys.options, queryFn: deedsApi.options, retry: false })
}

/** Refresh everything a changed deed shows up in (§5.1). */
export function useInvalidateDeeds() {
  const queryClient = useQueryClient()
  return (deed?: DeedOut) => {
    if (deed) queryClient.setQueryData(deedsKeys.deed(deed.id), deed)
    void queryClient.invalidateQueries({ queryKey: ['deeds', 'mine'] })
    void queryClient.invalidateQueries({ queryKey: deedsKeys.options })
    void queryClient.invalidateQueries({ queryKey: ['deeds', 'counts'] })
  }
}

/** `POST /deeds/{id}/withdraw`. */
export function useWithdrawDeed(onDone?: (deed: DeedOut) => void) {
  const invalidate = useInvalidateDeeds()
  return useMutation({
    mutationFn: (id: string) => deedsApi.withdrawDeed(id),
    onSuccess: (deed) => {
      invalidate(deed)
      onDone?.(deed)
    },
  })
}
