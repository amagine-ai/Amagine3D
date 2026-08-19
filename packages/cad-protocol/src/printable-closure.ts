const PRINTABLE_CLOSURE_INTENT =
  /(?:\b(?:hinge|hinged|lid|door|latch|closure|clamshell|openable|snap-fit|slide-on lid|sliding lid|bayonet|threaded (?:lid|cap)|magnetic lid|living hinge|flexible strap)\b|铰链|合页|开合|启闭|盒盖|箱盖|盖子|上盖|下盖|翻盖|滑盖|门板|舱门|卡扣|搭扣|卡口|旋盖|螺纹盖|磁吸盖|活铰链|柔性带)/iu;

export function containsPrintableClosureIntent(
  signals: Iterable<string>,
): boolean {
  return PRINTABLE_CLOSURE_INTENT.test([...signals].join('\n'));
}
