import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from '@tanstack/react-router';
import { useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';

import { loginMutation } from '@/shared/auth';

export function LoginPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const login = useMutation(loginMutation());

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    login.mutate(
      { body: { username, password } },
      {
        onSuccess: async () => {
          await queryClient.invalidateQueries();
          await navigate({ to: '/' });
        },
      },
    );
  };

  const inputClass = 'w-full rounded-md border border-line bg-surface px-3 py-2 text-sm';

  return (
    <main className="mx-auto mt-24 max-w-sm p-8">
      <h1 className="mb-6 text-2xl font-semibold">{t('auth.login.title')}</h1>
      <form onSubmit={onSubmit} className="space-y-4">
        <input
          value={username}
          onChange={(event) => {
            setUsername(event.target.value);
          }}
          placeholder={t('auth.login.username')}
          autoComplete="username"
          aria-label={t('auth.login.username')}
          className={inputClass}
        />
        <input
          type="password"
          value={password}
          onChange={(event) => {
            setPassword(event.target.value);
          }}
          placeholder={t('auth.login.password')}
          autoComplete="current-password"
          aria-label={t('auth.login.password')}
          className={inputClass}
        />
        {login.isError ? (
          <p role="alert" className="text-sm text-danger">
            {t('auth.login.error')}
          </p>
        ) : null}
        <button
          type="submit"
          disabled={login.isPending}
          className="w-full rounded-md bg-primary px-3 py-2 text-sm font-medium text-white hover:opacity-90 disabled:opacity-50"
        >
          {t('auth.login.submit')}
        </button>
      </form>
    </main>
  );
}
