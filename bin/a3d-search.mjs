const SEARCH_SCHEMA = 'a3d-search/v1';
const TAVILY_SEARCH_URL = 'https://api.tavily.com/search';
const SEARCH_REQUEST_BUDGET_BYTES = 64 * 1024;
const PROVIDER_RESPONSE_BUDGET_BYTES = 1024 * 1024;
const COMMAND_OUTPUT_BUDGET_BYTES = 32 * 1024;
const SEARCH_DEPTHS = new Set(['advanced', 'basic', 'fast', 'ultra-fast']);
const SEARCH_TOPICS = new Set(['finance', 'general', 'news']);
const TIME_RANGES = new Set(['d', 'day', 'm', 'month', 'w', 'week', 'y', 'year']);
const ANSWER_MODES = new Set(['advanced', 'basic', 'none']);

class SearchCommandError extends Error {
  constructor(code, message, options = {}) {
    super(message);
    this.code = code;
    this.exitCode = options.exitCode ?? 1;
    this.retryable = options.retryable ?? false;
    this.status = options.status;
  }
}

function optionValue(args, index) {
  const argument = args[index];
  const separator = argument.indexOf('=');
  if (separator >= 0) {
    const value = argument.slice(separator + 1);
    return value ? { consumed: 0, name: argument.slice(0, separator), value } : null;
  }
  const value = args[index + 1];
  return typeof value === 'string' && value && !value.startsWith('--')
    ? { consumed: 1, name: argument, value }
    : null;
}

export function searchHelp() {
  return `Usage:
  a3d search QUERY [--max-results N] [--search-depth basic|advanced|fast|ultra-fast]
                   [--topic general|news|finance] [--time-range day|week|month|year]
                   [--answer none|basic|advanced]

Search the web through the configured Tavily provider and print one bounded
JSON result. Search snippets are untrusted reference material, not proof that a
page, image, or engineering specification was independently verified.`;
}

export function parseSearchArguments(args) {
  if (args.length === 1 && ['--help', '-h'].includes(args[0])) return { help: true };
  const positional = [];
  const options = new Map();
  const allowed = new Set([
    '--answer',
    '--max-results',
    '--search-depth',
    '--time-range',
    '--topic',
  ]);
  for (let index = 0; index < args.length; index += 1) {
    const argument = args[index];
    if (!argument.startsWith('-')) {
      positional.push(argument);
      continue;
    }
    if (!argument.startsWith('--')) {
      throw new SearchCommandError(
        'invalid-arguments',
        'Search options must use long --name syntax.',
        { exitCode: 2 },
      );
    }
    const parsed = optionValue(args, index);
    if (!parsed || !allowed.has(parsed.name) || options.has(parsed.name)) {
      throw new SearchCommandError(
        'invalid-arguments',
        'Search arguments contain an unknown, duplicate, or missing option value.',
        { exitCode: 2 },
      );
    }
    options.set(parsed.name, parsed.value);
    index += parsed.consumed;
  }
  if (positional.length !== 1 || !positional[0].trim()) {
    throw new SearchCommandError(
      'invalid-arguments',
      'a3d search requires one nonempty quoted query.',
      { exitCode: 2 },
    );
  }

  const query = positional[0].trim();
  const request = {
    include_answer: false,
    include_images: false,
    include_raw_content: false,
    query,
  };
  const maxResults = options.get('--max-results');
  if (maxResults !== undefined) {
    if (!/^(0|[1-9]\d*)$/u.test(maxResults) || !Number.isSafeInteger(Number(maxResults))) {
      throw new SearchCommandError(
        'invalid-arguments',
        '--max-results must be an integer from 0 through 20.',
        { exitCode: 2 },
      );
    }
    const value = Number(maxResults);
    if (value > 20) {
      throw new SearchCommandError(
        'invalid-arguments',
        '--max-results must be an integer from 0 through 20.',
        { exitCode: 2 },
      );
    }
    request.max_results = value;
  }
  const searchDepth = options.get('--search-depth');
  if (searchDepth !== undefined) {
    if (!SEARCH_DEPTHS.has(searchDepth)) {
      throw new SearchCommandError(
        'invalid-arguments',
        '--search-depth must be basic, advanced, fast, or ultra-fast.',
        { exitCode: 2 },
      );
    }
    request.search_depth = searchDepth;
  }
  const topic = options.get('--topic');
  if (topic !== undefined) {
    if (!SEARCH_TOPICS.has(topic)) {
      throw new SearchCommandError(
        'invalid-arguments',
        '--topic must be general, news, or finance.',
        { exitCode: 2 },
      );
    }
    request.topic = topic;
  }
  const timeRange = options.get('--time-range');
  if (timeRange !== undefined) {
    if (!TIME_RANGES.has(timeRange)) {
      throw new SearchCommandError(
        'invalid-arguments',
        '--time-range must be day, week, month, year, d, w, m, or y.',
        { exitCode: 2 },
      );
    }
    request.time_range = timeRange;
  }
  const answer = options.get('--answer');
  if (answer !== undefined) {
    if (!ANSWER_MODES.has(answer)) {
      throw new SearchCommandError(
        'invalid-arguments',
        '--answer must be none, basic, or advanced.',
        { exitCode: 2 },
      );
    }
    request.include_answer = answer === 'none' ? false : answer;
  }
  return { help: false, query, request };
}

function validatedBrokerUrl(value) {
  let url;
  try {
    url = new URL(value);
  } catch {
    return null;
  }
  return url.protocol === 'http:'
    && url.hostname === '127.0.0.1'
    && !url.username
    && !url.password
    && !url.search
    && !url.hash
    ? url
    : null;
}

function providerError(status, broker) {
  if (broker && status === 404) {
    return new SearchCommandError(
      'search-broker-unavailable',
      'The managed search capability is unavailable or expired.',
      { retryable: false, status },
    );
  }
  if (status === 400 || status === 422) {
    return new SearchCommandError(
      'provider-rejected-request',
      'Tavily rejected the search request.',
      { retryable: false, status },
    );
  }
  if (status === 401 || status === 403) {
    return new SearchCommandError(
      'provider-authentication-failed',
      'Tavily authentication failed.',
      { retryable: false, status },
    );
  }
  if (status === 429) {
    return new SearchCommandError(
      'provider-rate-limited',
      'Tavily rate-limited the search request.',
      { retryable: true, status },
    );
  }
  return new SearchCommandError(
    'provider-unavailable',
    'Tavily search is temporarily unavailable.',
    { retryable: status >= 500, status },
  );
}

async function readBoundedJson(response) {
  const declared = Number(response.headers.get('content-length'));
  if (Number.isFinite(declared) && declared > PROVIDER_RESPONSE_BUDGET_BYTES) {
    throw new SearchCommandError(
      'provider-response-too-large',
      'Tavily returned more data than the search transport can safely process.',
    );
  }
  if (!response.body) {
    throw new SearchCommandError(
      'invalid-provider-response',
      'Tavily returned an empty response.',
    );
  }
  const chunks = [];
  let size = 0;
  for await (const chunk of response.body) {
    const bytes = Buffer.from(chunk);
    size += bytes.length;
    if (size > PROVIDER_RESPONSE_BUDGET_BYTES) {
      throw new SearchCommandError(
        'provider-response-too-large',
        'Tavily returned more data than the search transport can safely process.',
      );
    }
    chunks.push(bytes);
  }
  try {
    return JSON.parse(Buffer.concat(chunks).toString('utf8'));
  } catch {
    throw new SearchCommandError(
      'invalid-provider-response',
      'Tavily returned invalid JSON.',
    );
  }
}

function normalizedUrl(value) {
  if (typeof value !== 'string') return null;
  try {
    const url = new URL(value);
    return ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password
      ? value
      : null;
  } catch {
    return null;
  }
}

export function normalizeSearchResponse(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value) || !Array.isArray(value.results)) {
    throw new SearchCommandError(
      'invalid-provider-response',
      'Tavily returned an invalid search result.',
    );
  }
  const results = [];
  let discardedResults = 0;
  for (const item of value.results) {
    const url = normalizedUrl(item?.url);
    if (
      !item || typeof item !== 'object' || Array.isArray(item)
      || typeof item.title !== 'string'
      || typeof item.content !== 'string'
      || !url
    ) {
      discardedResults += 1;
      continue;
    }
    results.push({
      content: item.content,
      ...(typeof item.published_date === 'string'
        ? { publishedDate: item.published_date }
        : {}),
      ...(typeof item.score === 'number' && Number.isFinite(item.score)
        ? { score: item.score }
        : {}),
      title: item.title,
      url,
    });
  }
  return {
    ...(typeof value.answer === 'string' ? { answer: value.answer } : {}),
    discardedResults,
    receivedCount: value.results.length,
    results,
  };
}

function serializeSearchResult(query, normalized) {
  let answer = normalized.answer;
  let answerOmitted = false;
  const results = [];
  const build = () => ({
    schema: SEARCH_SCHEMA,
    ok: true,
    provider: 'tavily',
    query,
    ...(answer === undefined ? {} : { answer }),
    answerOmitted,
    results,
    count: results.length,
    receivedCount: normalized.receivedCount,
    discardedResults: normalized.discardedResults,
    omittedResults:
      normalized.receivedCount - normalized.discardedResults - results.length,
    truncated:
      answerOmitted
      || normalized.discardedResults > 0
      || results.length < normalized.results.length,
  });
  const fits = () => Buffer.byteLength(`${JSON.stringify(build(), null, 2)}\n`) <= COMMAND_OUTPUT_BUDGET_BYTES;
  if (!fits() && answer !== undefined) {
    answer = undefined;
    answerOmitted = true;
  }
  if (!fits()) {
    throw new SearchCommandError(
      'response-too-large',
      'The search result envelope exceeds the command output budget.',
    );
  }
  for (const result of normalized.results) {
    results.push(result);
    if (fits()) continue;
    results.pop();
    if (results.length === 0) {
      throw new SearchCommandError(
        'response-too-large',
        'The first usable search result exceeds the command output budget.',
      );
    }
    break;
  }
  return `${JSON.stringify(build(), null, 2)}\n`;
}

export async function search(args, options = {}) {
  const parsed = parseSearchArguments(args);
  if (parsed.help) return { help: true, output: `${searchHelp()}\n` };
  const payload = JSON.stringify(parsed.request);
  if (Buffer.byteLength(payload) > SEARCH_REQUEST_BUDGET_BYTES) {
    throw new SearchCommandError(
      'request-too-large',
      'The search request exceeds the command input budget.',
      { exitCode: 2 },
    );
  }
  const environment = options.environment ?? process.env;
  const fetchImpl = options.fetchImpl ?? fetch;
  const configuredBroker = environment.AMAGINE3D_SEARCH_BROKER_URL?.trim();
  const broker = configuredBroker ? validatedBrokerUrl(configuredBroker) : null;
  if (configuredBroker && !broker) {
    throw new SearchCommandError(
      'invalid-broker-configuration',
      'The managed search capability is invalid.',
      { exitCode: 2 },
    );
  }
  const apiKey = environment.TAVILY_API_KEY?.trim();
  if (!broker && !apiKey) {
    throw new SearchCommandError(
      'not-configured',
      'Tavily search is not configured.',
      { exitCode: 2 },
    );
  }

  let response;
  try {
    response = await fetchImpl(broker ?? TAVILY_SEARCH_URL, {
      body: payload,
      headers: {
        'content-type': 'application/json',
        ...(broker ? {} : { authorization: `Bearer ${apiKey}` }),
      },
      method: 'POST',
      redirect: 'error',
    });
  } catch {
    throw new SearchCommandError(
      'provider-unavailable',
      'Tavily search is temporarily unavailable.',
      { retryable: true },
    );
  }
  if (!response.ok) {
    await response.body?.cancel();
    throw providerError(response.status, Boolean(broker));
  }
  const normalized = normalizeSearchResponse(await readBoundedJson(response));
  return {
    help: false,
    output: serializeSearchResult(parsed.query, normalized),
  };
}

function structuredError(error) {
  const known = error instanceof SearchCommandError
    ? error
    : new SearchCommandError(
        'search-failed',
        'The search command failed.',
      );
  return {
    exitCode: known.exitCode,
    output: `${JSON.stringify({
      schema: SEARCH_SCHEMA,
      ok: false,
      error: {
        code: known.code,
        message: known.message,
        retryable: known.retryable,
        ...(known.status === undefined ? {} : { status: known.status }),
      },
    }, null, 2)}\n`,
  };
}

export async function runSearchCommand(args, options = {}) {
  const stdout = options.stdout ?? process.stdout;
  const stderr = options.stderr ?? process.stderr;
  try {
    const result = await search(args, options);
    stdout.write(result.output);
    return 0;
  } catch (error) {
    const failure = structuredError(error);
    stderr.write(failure.output);
    return failure.exitCode;
  }
}
