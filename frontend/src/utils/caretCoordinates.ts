/**
 * Get the (x, y) coordinates of the caret in a textarea or input.
 * Adapted from: https://github.com/component/textarea-caret-position
 */

export interface CaretCoordinates {
  top: number;
  left: number;
  height: number;
}

const properties = [
  'direction',
  'boxSizing',
  'width',
  'height',
  'overflowX',
  'overflowY',
  'borderTopWidth',
  'borderRightWidth',
  'borderBottomWidth',
  'borderLeftWidth',
  'borderStyle',
  'paddingTop',
  'paddingRight',
  'paddingBottom',
  'paddingLeft',
  'fontStyle',
  'fontVariant',
  'fontWeight',
  'fontStretch',
  'fontSize',
  'fontSizeAdjust',
  'lineHeight',
  'fontFamily',
  'textAlign',
  'textTransform',
  'textIndent',
  'textDecoration',
  'letterSpacing',
  'wordSpacing',
  'tabSize',
  'MozTabSize',
] as const;

export function getCaretCoordinates(
  element: HTMLTextAreaElement,
  position: number
): CaretCoordinates {
  if (typeof document === 'undefined') {
    return { top: 0, left: 0, height: 0 };
  }

  // The mirror div will replicate the textarea's styles
  const div = document.createElement('div');
  div.id = 'input-textarea-caret-position-mirror-div';
  document.body.appendChild(div);

  const style = div.style;
  const computed = window.getComputedStyle(element);

  // Default wrapping and whitespace behavior
  style.whiteSpace = 'pre-wrap';
  style.wordWrap = 'break-word';

  // Position off-screen
  style.position = 'absolute';
  style.visibility = 'hidden';

  // Replicate styles
  properties.forEach((prop) => {
    (style as any)[prop] = computed.getPropertyValue(prop);
  });

  const isFirefox = typeof (window as any).mozInnerScreenX !== 'undefined';
  if (isFirefox) {
    if (element.scrollHeight > element.clientHeight) {
      style.overflowY = 'scroll';
    }
  } else {
    style.overflowY = 'hidden';
  }

  div.textContent = element.value.substring(0, position);

  const span = document.createElement('span');
  span.textContent = element.value.substring(position) || '.';
  div.appendChild(span);

  const coordinates = {
    top: span.offsetTop + parseInt(computed.borderTopWidth),
    left: span.offsetLeft + parseInt(computed.borderLeftWidth),
    height: parseInt(computed.lineHeight || '0'),
  };

  document.body.removeChild(div);

  return coordinates;
}
