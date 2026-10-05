import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from '@tanstack/react-router';
import { useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';

import { loginMutation } from '@/shared/auth';
import { resetLogEventStreamSession } from '@/shared/lib';
import { Button, Input } from '@/shared/ui';

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
          resetLogEventStreamSession();
          await queryClient.invalidateQueries();
          await navigate({ to: '/' });
        },
      },
    );
  };

  return (
    // eslint-disable-next-line design-tokens/no-raw-values -- see the note in the rule: the login card, the only page in the app built as one
    <main className="mx-auto mt-[96px] max-w-[384px] p-8">
      <h1 className="mb-6 type-h1">{t('auth.login.title')}</h1>
      <form onSubmit={onSubmit} className="space-y-4">
        <Input
          value={username}
          onChange={(event) => {
            setUsername(event.target.value);
          }}
          placeholder={t('auth.login.username')}
          autoComplete="username"
          aria-label={t('auth.login.username')}
          className="w-full"
        />
        <Input
          type="password"
          value={password}
          onChange={(event) => {
            setPassword(event.target.value);
          }}
          placeholder={t('auth.login.password')}
          autoComplete="current-password"
          aria-label={t('auth.login.password')}
          className="w-full"
        />
        {login.isError ? (
          <p role="alert" className="type-body text-danger">
            {t('auth.login.error')}
          </p>
        ) : null}
        <Button
          type="submit"
          variant="primary"
          fullWidth
          className="font-medium"
          disabled={login.isPending}
        >
          {t('auth.login.submit')}
        </Button>
      </form>
    </main>
  );
}
