// The device farm's data-access surface: the phone list and its two actions,
// wrapped from the generated client so pages depend on the entity.
export {
  listPhonesOptions as phonesQueryOptions,
  listPhonesQueryKey as phonesQueryKey,
  startPhoneMutation,
  stopPhoneMutation,
} from '@/shared/api/@tanstack/react-query.gen';
