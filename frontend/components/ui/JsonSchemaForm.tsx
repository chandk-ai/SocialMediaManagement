'use client';
/**
 * Renders a small subset of JSON Schema as a friendly form.
 * Supported keywords: type (string/integer/number/boolean/array of strings),
 * format (uri / password), default, title, description, required, minimum,
 * minItems, enum.
 *
 * Out of scope: nested objects, oneOf, dependencies. Keep it small and
 * predictable so adding a new source plugin doesn't require UI changes.
 */
import { useEffect, useMemo, useState } from 'react';
import { Input } from '@/components/ui/Input';

type Schema = {
  type?: string;
  required?: string[];
  properties?: Record<string, FieldSchema>;
};
type FieldSchema = {
  type?: 'string' | 'integer' | 'number' | 'boolean' | 'array' | 'object';
  format?: string;
  title?: string;
  description?: string;
  default?: unknown;
  minimum?: number;
  maximum?: number;
  minItems?: number;
  enum?: (string | number)[];
  items?: FieldSchema;
};

export function JsonSchemaForm({
  schema,
  value,
  onChange,
}: {
  schema: Schema | undefined;
  value: Record<string, unknown>;
  onChange: (v: Record<string, unknown>) => void;
}) {
  const fields = useMemo(() => Object.entries(schema?.properties ?? {}), [schema]);
  const required = new Set(schema?.required ?? []);

  // Seed defaults the first time a schema mounts
  useEffect(() => {
    if (!schema?.properties) return;
    const seeded: Record<string, unknown> = { ...value };
    let changed = false;
    for (const [key, prop] of Object.entries(schema.properties)) {
      if (seeded[key] === undefined && prop.default !== undefined) {
        seeded[key] = prop.default;
        changed = true;
      }
    }
    if (changed) onChange(seeded);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [schema]);

  if (!schema?.properties || fields.length === 0) {
    return (
      <p className="text-xs text-ink-500">
        No configuration fields are required for this plugin.
      </p>
    );
  }

  function set(key: string, v: unknown) {
    onChange({ ...value, [key]: v });
  }

  return (
    <div className="space-y-4">
      {fields.map(([key, prop]) => (
        <Field
          key={key}
          name={key}
          schema={prop}
          required={required.has(key)}
          value={value[key]}
          onChange={(v) => set(key, v)}
        />
      ))}
    </div>
  );
}

function Field({
  name, schema, required, value, onChange,
}: {
  name: string;
  schema: FieldSchema;
  required: boolean;
  value: unknown;
  onChange: (v: unknown) => void;
}) {
  const label = schema.title || prettifyKey(name);
  const description = schema.description;

  // enum → select
  if (schema.enum && schema.enum.length) {
    return (
      <Wrapper label={label} required={required} description={description}>
        <select
          className="input"
          value={(value as any) ?? ''}
          onChange={(e) => onChange(e.target.value)}
        >
          <option value="">Select…</option>
          {schema.enum.map((v) => (
            <option key={String(v)} value={String(v)}>
              {String(v)}
            </option>
          ))}
        </select>
      </Wrapper>
    );
  }

  // boolean → toggle
  if (schema.type === 'boolean') {
    return (
      <Wrapper label={label} required={required} description={description}>
        <label className="inline-flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={!!value}
            onChange={(e) => onChange(e.target.checked)}
          />
          <span>{value ? 'enabled' : 'disabled'}</span>
        </label>
      </Wrapper>
    );
  }

  // integer / number
  if (schema.type === 'integer' || schema.type === 'number') {
    return (
      <Wrapper label={label} required={required} description={description}>
        <Input
          type="number"
          value={value === undefined || value === null ? '' : String(value)}
          min={schema.minimum}
          max={schema.maximum}
          onChange={(e) => {
            const v = e.target.value;
            if (v === '') return onChange(undefined);
            onChange(schema.type === 'integer' ? parseInt(v, 10) : parseFloat(v));
          }}
        />
      </Wrapper>
    );
  }

  // array of strings → tags input (one per line)
  if (schema.type === 'array' && schema.items?.type === 'string') {
    const arr = Array.isArray(value) ? (value as string[]) : [];
    return (
      <Wrapper label={label} required={required} description={description ?? 'One value per line.'}>
        <textarea
          className="input min-h-[80px] font-mono text-xs"
          value={arr.join('\n')}
          onChange={(e) => {
            const lines = e.target.value
              .split('\n')
              .map((s) => s.trim())
              .filter(Boolean);
            onChange(lines);
          }}
        />
      </Wrapper>
    );
  }

  // password
  if (schema.format === 'password') {
    return (
      <Wrapper label={label} required={required} description={description}>
        <Input
          type="password"
          value={(value as any) ?? ''}
          onChange={(e) => onChange(e.target.value)}
        />
      </Wrapper>
    );
  }

  // default: string / uri / email
  return (
    <Wrapper label={label} required={required} description={description}>
      <Input
        type={schema.format === 'email' ? 'email' : 'text'}
        placeholder={schema.format === 'uri' ? 'https://…' : undefined}
        value={(value as any) ?? ''}
        onChange={(e) => onChange(e.target.value)}
      />
    </Wrapper>
  );
}

function Wrapper({
  label, required, description, children,
}: {
  label: string;
  required: boolean;
  description?: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <label className="block text-xs text-ink-700 mb-1 font-medium">
        {label} {required && <span className="text-red-600">*</span>}
      </label>
      {children}
      {description && (
        <p className="text-[11px] text-ink-500 mt-1">{description}</p>
      )}
    </div>
  );
}

function prettifyKey(k: string): string {
  return k.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}
