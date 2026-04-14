import { useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import axios from 'axios';
import { Button } from '../components/ui/button';
import { Input } from '../components/ui/input';
import { Label } from '../components/ui/label';
import { Card, CardContent, CardHeader, CardTitle } from '../components/ui/card';
import { BarChart3, ArrowLeft, KeyRound, Eye, EyeOff, CheckCircle2, AlertTriangle } from 'lucide-react';

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;

export default function ResetPasswordPage() {
  const [searchParams] = useSearchParams();
  const token = searchParams.get('token') || '';
  const navigate = useNavigate();

  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [showPw, setShowPw] = useState(false);
  const [showConfirm, setShowConfirm] = useState(false);
  const [loading, setLoading] = useState(false);
  const [success, setSuccess] = useState(false);
  const [error, setError] = useState('');

  const passwordsMatch = newPassword && confirmPassword && newPassword === confirmPassword;
  const passwordTooShort = newPassword && newPassword.length < 8;

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!passwordsMatch) {
      setError('Passwords do not match');
      return;
    }
    if (passwordTooShort) {
      setError('Password must be at least 8 characters');
      return;
    }
    setError('');
    setLoading(true);
    try {
      await axios.post(`${BACKEND_URL}/api/auth/reset-password`, {
        token,
        new_password: newPassword,
      });
      setSuccess(true);
      setTimeout(() => navigate('/login'), 3000);
    } catch (err) {
      setError(err.response?.data?.detail || 'Failed to reset password. The link may have expired.');
    }
    setLoading(false);
  };

  if (!token) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-[#060D1A]" style={{ fontFamily: 'Manrope, sans-serif' }}>
        <Card className="border-white/5 bg-white/[0.04] text-white shadow-2xl max-w-md w-full mx-4">
          <CardContent className="pt-8 pb-8 text-center">
            <AlertTriangle size={40} className="text-amber-400 mx-auto mb-4" />
            <p className="text-white font-semibold text-base mb-2">Invalid Reset Link</p>
            <p className="text-slate-400 text-sm mb-6">
              This password reset link is missing or invalid. Please request a new one.
            </p>
            <Link to="/forgot-password">
              <Button className="w-full h-11 bg-emerald-500 hover:bg-emerald-400 text-white font-bold rounded-xl">
                Request New Link
              </Button>
            </Link>
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div className="min-h-screen flex bg-[#060D1A]" style={{ fontFamily: 'Manrope, sans-serif' }}>
      {/* Left branding */}
      <div className="hidden lg:flex lg:w-5/12 flex-col justify-between p-12 border-r border-white/5">
        <div>
          <Link to="/login" className="flex items-center gap-2 text-slate-400 hover:text-white transition-colors mb-12">
            <ArrowLeft size={16} />
            <span className="text-sm">Back to sign in</span>
          </Link>
          <div className="flex items-center gap-3 mb-12">
            <div className="w-10 h-10 bg-emerald-500 rounded-xl flex items-center justify-center">
              <BarChart3 size={20} className="text-white" />
            </div>
            <span className="text-white font-bold text-xl">AgriBooks</span>
          </div>
          <h2 className="text-3xl font-extrabold text-white mb-3">Set a new password</h2>
          <p className="text-slate-400 text-sm leading-relaxed">
            Choose a strong password. At least 8 characters.
          </p>
        </div>
      </div>

      {/* Right form */}
      <div className="flex-1 flex items-center justify-center p-6">
        <div className="w-full max-w-md">
          <Link to="/login" className="flex lg:hidden items-center gap-2 text-slate-400 hover:text-white transition-colors mb-8">
            <ArrowLeft size={16} />
            <span className="text-sm">Back to sign in</span>
          </Link>

          <Card className="border-white/5 bg-white/[0.04] text-white shadow-2xl">
            <CardHeader className="text-center pb-4">
              <div className="w-12 h-12 rounded-xl bg-emerald-500/10 border border-emerald-500/20 flex items-center justify-center mx-auto mb-3">
                <KeyRound size={22} className="text-emerald-400" />
              </div>
              <CardTitle className="text-xl text-white">Create New Password</CardTitle>
              <p className="text-sm text-slate-400 mt-1">Must be at least 8 characters</p>
            </CardHeader>
            <CardContent>
              {success ? (
                <div className="text-center py-4" data-testid="reset-password-success">
                  <CheckCircle2 size={40} className="text-emerald-400 mx-auto mb-4" />
                  <p className="text-white font-semibold text-base mb-2">Password Reset!</p>
                  <p className="text-slate-400 text-sm leading-relaxed mb-6">
                    Your password has been updated successfully.<br />
                    Redirecting you to sign in...
                  </p>
                  <Link to="/login">
                    <Button className="w-full h-11 bg-emerald-500 hover:bg-emerald-400 text-white font-bold rounded-xl">
                      Sign In Now
                    </Button>
                  </Link>
                </div>
              ) : (
                <form onSubmit={handleSubmit} className="space-y-4">
                  {error && (
                    <div data-testid="reset-password-error" className="bg-red-500/10 border border-red-500/20 text-red-400 text-sm px-4 py-3 rounded-lg">
                      {error}
                    </div>
                  )}

                  {/* New password */}
                  <div className="space-y-1.5">
                    <Label htmlFor="new-password" className="text-slate-300 text-sm">New Password</Label>
                    <div className="relative">
                      <Input
                        id="new-password"
                        data-testid="reset-new-password"
                        type={showPw ? 'text' : 'password'}
                        value={newPassword}
                        onChange={e => setNewPassword(e.target.value)}
                        placeholder="Minimum 8 characters"
                        className="bg-white/5 border-white/10 text-white placeholder:text-slate-600 h-11 pr-10"
                        required
                        autoComplete="new-password"
                        autoFocus
                      />
                      <button
                        type="button"
                        onClick={() => setShowPw(!showPw)}
                        className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-500 hover:text-slate-300"
                      >
                        {showPw ? <EyeOff size={15} /> : <Eye size={15} />}
                      </button>
                    </div>
                    {passwordTooShort && (
                      <p className="text-red-400 text-xs mt-1">At least 8 characters required</p>
                    )}
                  </div>

                  {/* Confirm password */}
                  <div className="space-y-1.5">
                    <Label htmlFor="confirm-password" className="text-slate-300 text-sm">Confirm Password</Label>
                    <div className="relative">
                      <Input
                        id="confirm-password"
                        data-testid="reset-confirm-password"
                        type={showConfirm ? 'text' : 'password'}
                        value={confirmPassword}
                        onChange={e => setConfirmPassword(e.target.value)}
                        placeholder="Re-enter your password"
                        className={`bg-white/5 border-white/10 text-white placeholder:text-slate-600 h-11 pr-10 ${
                          confirmPassword && !passwordsMatch ? 'border-red-500/50' : ''
                        } ${passwordsMatch ? 'border-emerald-500/50' : ''}`}
                        required
                        autoComplete="new-password"
                      />
                      <button
                        type="button"
                        onClick={() => setShowConfirm(!showConfirm)}
                        className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-500 hover:text-slate-300"
                      >
                        {showConfirm ? <EyeOff size={15} /> : <Eye size={15} />}
                      </button>
                    </div>
                    {confirmPassword && !passwordsMatch && (
                      <p className="text-red-400 text-xs mt-1">Passwords don't match</p>
                    )}
                    {passwordsMatch && (
                      <p className="text-emerald-400 text-xs mt-1">Passwords match</p>
                    )}
                  </div>

                  <Button
                    type="submit"
                    data-testid="reset-password-submit"
                    disabled={loading || !newPassword || !confirmPassword || !passwordsMatch || !!passwordTooShort}
                    className="w-full h-11 bg-emerald-500 hover:bg-emerald-400 text-white font-bold rounded-xl"
                  >
                    {loading ? 'Resetting...' : 'Reset Password'}
                  </Button>

                  <div className="text-center">
                    <Link to="/forgot-password" className="text-slate-500 hover:text-slate-300 text-sm transition-colors">
                      Request a new link
                    </Link>
                  </div>
                </form>
              )}
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}
