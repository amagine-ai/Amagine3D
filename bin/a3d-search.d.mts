interface SearchRequest {
  include_answer: false | 'advanced' | 'basic';
  include_images: false;
  include_raw_content: false;
  max_results?: number;
  query: string;
  search_depth?: 'advanced' | 'basic' | 'fast' | 'ultra-fast';
  time_range?: 'd' | 'day' | 'm' | 'month' | 'w' | 'week' | 'y' | 'year';
  topic?: 'finance' | 'general' | 'news';
}

interface SearchCommandOptions {
  environment?: NodeJS.ProcessEnv;
  fetchImpl?: typeof fetch;
  stderr?: { write(chunk: string): unknown };
  stdout?: { write(chunk: string): unknown };
}

interface NormalizedSearchResult {
  content: string;
  publishedDate?: string;
  score?: number;
  title: string;
  url: string;
}

export function searchHelp(): string;
export function parseSearchArguments(args: string[]):
  | { help: true }
  | { help: false; query: string; request: SearchRequest };
export function normalizeSearchResponse(value: unknown): {
  answer?: string;
  discardedResults: number;
  receivedCount: number;
  results: NormalizedSearchResult[];
};
export function search(
  args: string[],
  options?: SearchCommandOptions,
): Promise<{ help: boolean; output: string }>;
export function runSearchCommand(
  args: string[],
  options?: SearchCommandOptions,
): Promise<number>;
