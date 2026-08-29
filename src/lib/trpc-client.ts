import { createTRPCClient, httpBatchLink } from '@trpc/client';

import type { AppRouter } from '../../server/trpc/router.ts';

export const trpc = createTRPCClient<AppRouter>({
  links: [
    httpBatchLink({
      url: '/trpc',
    }),
  ],
});
