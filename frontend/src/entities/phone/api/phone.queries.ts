// The device farm's data-access surface: the phone list, its two actions and the
// independent emulators' own writes, wrapped from the generated client so pages
// depend on the entity.
export {
  assignStandaloneProxyMutation,
  createStandalonePhoneMutation,
  deleteStandalonePhoneMutation,
  listPhonesOptions as phonesQueryOptions,
  listPhonesQueryKey as phonesQueryKey,
  startPhoneMutation,
  stopPhoneMutation,
  unassignStandaloneProxyMutation,
} from '@/shared/api/@tanstack/react-query.gen';
