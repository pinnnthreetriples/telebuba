import { pageFrame, pageTitleSpacing, sectionStack } from '@/shared/design-system';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from '@tanstack/react-router';
import { useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';

import { loginMutation } from '@/shared/auth';
import { cn, resetLogEventStreamSession } from '@/shared/lib';
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
    <main className={pageFrame('auth')}>
      <h1 className={cn(pageTitleSpacing('auth'), 'type-page-title')}>{t('auth.login.title')}</h1>
      <form onSubmit={onSubmit} className={sectionStack('default', 'space')}>
        <Input
          value={username}
          onChange={(event) => {
            setUsername(event.target.value);
          }}
          placeholder={t('auth.login.username')}
          autoComplete="username"
          aria-label={t('auth.login.username')}
          widthPreset="full"
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
          widthPreset="full"
        />
        {login.isError ? (
          <p role="alert" className="type-dialog-body text-danger">
            {t('auth.login.error')}
          </p>
        ) : null}
        <Button
          type="submit"
          variant="primary"
          fullWidth
          weight="medium"
          disabled={login.isPending}
        >
          {t('auth.login.submit')}
        </Button>
      </form>
    </main>
  );
}
