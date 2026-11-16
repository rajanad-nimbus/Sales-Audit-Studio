'use client';

import { useCallback, useEffect, useId, useRef, useState } from 'react';
import { Button } from './Button';
import { Modal } from './Overlay';

const MAX_NAME = 60;

interface Request {
  title: string;
  existing: string[];
  resolve: (name: string | null) => void;
}

interface FormProps {
  formId: string;
  existing: string[];
  onSubmit: (name: string) => void;
  onValidity: (valid: boolean) => void;
}

function NameForm({ formId, existing, onSubmit, onValidity }: FormProps) {
  const [value, setValue] = useState('');
  const input = useRef<HTMLInputElement>(null);
  const name = value.trim();
  const tooLong = name.length > MAX_NAME;
  const replaces = existing.find((item) => item.toLocaleLowerCase() === name.toLocaleLowerCase());
  const valid = name.length > 0 && !tooLong;

  // The dialog focuses its panel on open; move focus to the field once that has happened.
  useEffect(() => {
    const frame = requestAnimationFrame(() => input.current?.focus());
    return () => cancelAnimationFrame(frame);
  }, []);
  useEffect(() => onValidity(valid), [valid, onValidity]);

  const message = tooLong
    ? `Use ${MAX_NAME} characters or fewer. This name has ${name.length}.`
    : replaces
      ? `Replaces the saved view "${replaces}".`
      : 'Saves the filters, sorting and page size you have now.';

  return (
    <form
      id={formId}
      onSubmit={(event) => {
        event.preventDefault();
        if (valid) onSubmit(name);
      }}
    >
      <label className="field">
        View name
        <input
          ref={input}
          value={value}
          onChange={(event) => setValue(event.target.value)}
          aria-invalid={tooLong}
          aria-describedby={`${formId}-hint`}
          autoComplete="off"
        />
        <span
          id={`${formId}-hint`}
          className={tooLong ? 'hint text-error' : 'hint'}
          role={tooLong ? 'alert' : undefined}
        >
          {message}
        </span>
      </label>
    </form>
  );
}

/**
 * Asks for a name in a dialog instead of the browser prompt. `ask` resolves with the trimmed
 * name, or `null` when the person cancels. Render `dialog` once anywhere in the component.
 */
export function useNameDialog() {
  const [request, setRequest] = useState<Request | null>(null);
  const [valid, setValid] = useState(false);
  const formId = useId();

  const ask = useCallback(
    (title: string, existing: string[] = []) =>
      new Promise<string | null>((resolve) => {
        setValid(false);
        setRequest({ title, existing, resolve });
      }),
    []
  );
  const pending = useRef<Request | null>(null);
  useEffect(() => {
    pending.current = request;
  }, [request]);
  const finish = useCallback((name: string | null) => {
    pending.current?.resolve(name);
    pending.current = null;
    setRequest(null);
  }, []);
  const cancel = useCallback(() => finish(null), [finish]);

  const dialog = request ? (
    <Modal
      title={request.title}
      onClose={cancel}
      footer={
        <>
          <Button onClick={cancel}>Cancel</Button>
          <Button variant="primary" type="submit" form={formId} disabled={!valid}>
            Save view
          </Button>
        </>
      }
    >
      <NameForm
        formId={formId}
        existing={request.existing}
        onSubmit={finish}
        onValidity={setValid}
      />
    </Modal>
  ) : null;

  return { ask, dialog };
}
