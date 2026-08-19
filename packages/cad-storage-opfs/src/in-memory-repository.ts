import { MemoryFileStore } from './file-store';
import {
  OpfsProjectRepository,
  type RepositoryOptions,
} from './opfs-repository';

export class InMemoryProjectRepository extends OpfsProjectRepository {
  constructor(
    options: RepositoryOptions = {},
    files: MemoryFileStore = new MemoryFileStore(),
  ) {
    super(files, options);
  }
}
