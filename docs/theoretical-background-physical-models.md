# Continuum in Lagrangian Framework

## Total Lagrangian Formulation for Solid Mechanics

Considering continuum mechanics in the total Lagrangian framework,
the kinematics and dynamic equations are expressed
in terms of the initial, undeformed reference configuration
$\Omega^0 \subset \boldsymbol{R}^d$ with $d$ denoting the dimension.
A deformation map $\varphi$ between the initial configuration $\Omega^0$ and
current deformed configuration $\Omega = \varphi \left( \Omega^0 \right)$
describes the body deformation at time $t$ as

\begin{equation}
\boldsymbol{r} = \varphi \left( \boldsymbol{r}^0, t \right),
\label{deformation-map}
\end{equation}

where $\boldsymbol{r}^0$ and $\boldsymbol{r}$ are the initial and
current positions of a material point, respectively.
Subsequently, the deformation gradient tensor $\boldsymbol{F}$ is given by

\begin{equation}
\boldsymbol{F} = \nabla^0 \boldsymbol{r} = \nabla^0 \boldsymbol{u} + \boldsymbol{I},
\label{deformation-gradient}
\end{equation}

where $\boldsymbol{u} = \boldsymbol{r} - \boldsymbol{r}^0$ is the displacement,
$\nabla^0 \equiv \frac{\partial}{\partial \boldsymbol{r}^0}$
the gradient operator with respect to the initial configuration
$\Omega^0$ and $\boldsymbol{I} $ the identity matrix.

The conservation equations for mass and momentum in
the total Lagrangian formulation can be expressed as

\begin{equation}
\begin{cases}
\rho = J^{-1}\rho^0 \\
\rho^0 \ddot {\boldsymbol{u}} = \nabla^0 \cdot \boldsymbol{P}^{\operatorname{T}},
\end{cases}
\label{conservation-equations}
\end{equation}

where $\rho^0$ and $\rho$ are the initial and current densities, respectively,
$J = \det(\boldsymbol{F})$,
$\ddot {\boldsymbol{u}}$ the acceleration,
$\boldsymbol{P}$ the first Piola-Kirchhoff stress tensor,
and $\operatorname{T}$ the matrix transposition operator.
While $\boldsymbol{P}$ can be obtained directly by

\begin{equation}
\boldsymbol{P} = \boldsymbol{F}\boldsymbol{S},
\label{piola-stress}
\end{equation}

where $\boldsymbol{S}$ is the second Piola-Kirchhoff stress tensor,
$\boldsymbol{P}$ is obtained by the alternative Kirchhoff-stress approach in this work as

\begin{equation}
\boldsymbol{P} = \boldsymbol{\tau}\boldsymbol{F}^{-\operatorname{T}}.
\label{kirchhoff-stress}
\end{equation}

Here, the Kirchhoff stress $\boldsymbol{\tau}$
is decomposed into volumetric and deviatoric components,
and can be derived form the following strain energy function \cite{simo2006computational}

\begin{equation}
\mathfrak{W}_e = \mathfrak{W}_v \left( J \right) + \mathfrak{W}_s \left(\bar {\boldsymbol{b}} \right).
\label{strain-energy}
\end{equation}

Here, the volume-preserving left Cauchy-Green deformation gradient tensor
$\bar  {\boldsymbol{b}} = J^ {-\frac{2}{d}}  \boldsymbol{b} = \left| \boldsymbol{b} \right|^{ - \frac{1}{d}} \boldsymbol{b}$ with $\boldsymbol{b} = \boldsymbol{F}\boldsymbol{F}^{\operatorname{T}}$.
For neo-Hookean materials,
the volume-dependent strain energy $\mathfrak{W}_v \left( J \right)$
weighted by the bulk modulus $K$ can be expressed as

\begin{equation}
\mathfrak{W}_v \left( J \right) = \frac{1}{2}K\left[ \frac{1}{2}\left( J^2 - 1 \right) - \ln J \right],
\label{volumetric-energy}
\end{equation}

whereas the shear-dependent strain energy $\mathfrak{W}_s \left(\bar  {\boldsymbol{b}} \right)$
weighted by the shear modulus $G$ \cite{yue2015continuum} is given by

\begin{equation}
\mathfrak{W}_s \left( \bar{ \boldsymbol{b}} \right) = \frac{1}{2} G \left( \operatorname{tr} \left( \bar {\boldsymbol{b}} \right) - d \right).
\label{deviatoric-energy}
\end{equation}

Then, the Kirchhoff stress tensor $\boldsymbol{\tau}$ can be derived as

\begin{equation}
\boldsymbol{\tau} = \frac{\partial \mathfrak{W}_e}{\partial \boldsymbol{F} } \boldsymbol{F}^{\operatorname{T}}  
 = \frac{K}{2}\left( J^2 - 1 \right) \boldsymbol{I} + G \operatorname{dev} \left( \bar {\boldsymbol{b}} \right),
\label{Kirchhoff_stress}
\end{equation}

where
\begin{equation}
\operatorname{dev} \left( \bar{ \boldsymbol{b}} \right)
= \bar {\boldsymbol{b}} - \frac{1}{d} \operatorname{tr} \left( \bar{ \boldsymbol{b}} \right) \mathbb{I}
= J^ {-\frac{2}{d}} \left[ \boldsymbol{b} - \frac{1}{d} \operatorname{tr} \left( \boldsymbol{b} \right) \mathbb{I} \right].
\label{deviatoric-stress}
\end{equation}

The deviatoric operator $\operatorname{dev}\left( \bar{ \boldsymbol{b}} \right)$
returns the trace-free part of $\bar{ \boldsymbol{b}}$,
i.e., $\operatorname{tr} \left( \operatorname{dev}\left( \bar{ \boldsymbol{b}} \right) \right)$ is equal to zero.
Note that while the volumetric component of the constitutive Eq. $\eqref{Kirchhoff_stress}$
can be modified depending on the material property
and all counterparts are appropriate for this study,
only the Eq. $\eqref{Kirchhoff_stress}$ is utilized in this study.

## Update Lagrangian Formulation for Solid Mechanics

The update Lagrangian formulation is an alternative approach to the total Lagrangian formulation.
In this framework, the kinematics and dynamic equations are expressed
in terms of the current, deformed configuration $\Omega \subset \boldsymbol{R}^d$. So the governing equations are very similar to those of fluid mechanics in Lagrangian framework.
Let $\rho$ denote the current mass density, $\mathbf{v}$ the velocity vector, $\mathbf{g}$ 
the body force per unit mass, and $\boldsymbol{\sigma}$ the Cauchy stress tensor. 
In the absence of thermal coupling, the local balance equations for mass and 
linear momentum are written as \cite{Borja2013,SimoHughes2006}

\begin{equation}
\frac{D\rho}{Dt}
+
\rho\nabla\cdot\mathbf{v}
=
0,
\label{eq:density}
\end{equation}

and

\begin{equation}
\rho\frac{D\mathbf{v}}{Dt} =
\nabla\cdot\boldsymbol{\sigma}
+
\rho\mathbf{g}.
\label{eq:moementum}
\end{equation}

Here, the Cauchy stress is decomposed into a deviatoric contribution and a hydrostatic contribution,

\begin{equation}
\boldsymbol{\sigma}
=
\mathbf{s}
-
p\mathbf{I},
\label{eq:CoxPnull}
\end{equation}

where $\mathbf{s}$ denotes the deviatoric stress tensor, $p$ is the pressure 
according to the sign convention adopted in fluid mechanics, and $\mathbf{I}$ is the 
second-order identity tensor. Consequently,

\[
\operatorname{tr}(\mathbf{s})=0,
\]

and the mean or hydrostatic stress becomes

\begin{equation}
\sigma_H
=
\frac{1}{3}\operatorname{tr}(\boldsymbol{\sigma})
= -p.
\label{eq:hydrostatic}
\end{equation}

This decomposition is particularly important for the present constitutive 
framework. Plastic yielding is governed only by the deviatoric stress through the $J_2$ criterion, whereas the hydrostatic stress does not influence yielding directly \cite{Borja2013,SimoHughes2006}. In contrast, hydrostatic stress plays an essential role in ductile damage because it promotes the growth of microscopic voids and defects 
and therefore enters the adopted damage formulation through stress triaxiality 
\cite{Kachanov1986,LemaitreChaboche2004,CaleyronEtAl2012}.

The velocity gradient is defined as

\begin{equation}
\mathbf{L}
=
\nabla\mathbf{v},
\label{eq:velocity-gradient}
\end{equation}

and is decomposed into its symmetric and antisymmetric parts,

\begin{equation}
\mathbf{D}
=
\frac{1}{2}
\left(
\mathbf{L}+\mathbf{L}^{T}
\right),
\qquad
\mathbf{W}
=
\frac{1}{2}
\left(
\mathbf{L}-\mathbf{L}^{T}
\right),
\label{eq:velocity-gradient-decomposition}
\end{equation}

where $\mathbf{D}$ is the rate-of-deformation tensor and $\mathbf{W}$ is the 
spin tensor. The deviatoric part of the rate-of-deformation tensor is

\begin{equation}
\mathbf{D}'
=
\mathbf{D}
-
\frac{1}{d}
\operatorname{tr}(\mathbf{D})\mathbf{I},
\label{eq:deviatoric-rate-of-deformation}
\end{equation}

where $d$ denotes the spatial dimension.

Since metal cutting involves large deformation and significant material 
rotation, an objective stress update is required. The updated-Lagrangian solid 
formulation employed in the underlying SPH framework uses a corotational stress 
update for the deviatoric stress \cite{ZhangEtAl2025}. In the elastic regime, the deviatoric 
stress rate is expressed as

\begin{equation}
\dot{\mathbf{s}}
=
2G\mathbf{D}'
+
\mathbf{s}\mathbf{W}^{T}
+
\mathbf{W}\mathbf{s},
\label{eq:corotational-stress-update}
\end{equation}

where

\begin{equation}
G
=
\frac{E}{2(1+\nu)}
\label{eq:shear-modulus}
\end{equation}

is the shear modulus, $E$ is Young's modulus, and $\nu$ is Poisson's ratio. 
The corresponding bulk modulus is

\begin{equation}
K
=
\frac{E}{3(1-2\nu)}.
\label{eq:bulk-modulus}
\end{equation}

These relations form the elastic part of the constitutive update employed by 
the J2 material model. They are consistent with the continuum-mechanics 
formulation of elastoplastic solids \cite{Borja2013,SimoHughes2006} and with the updated-Lagrangian SPH 
solid-dynamics formulation developed by Zhang et al.~\cite{ZhangEtAl2025}.

### J2 Plasticity

The popular elastic-plastic nonlinear material model, often used for metal, is the isotropic $J_2$ plasticity with linear isotropic hardening. The $J_2$, or von Mises, model is widely used for ductile 
metallic materials because plastic yielding is assumed to depend primarily on distortional deformation rather than on hydrostatic pressure \cite{Borja2013,SimoHughes2006}.

The second invariant of the deviatoric stress tensor is defined as

\begin{equation}
J_2
=
\frac{1}{2}\mathbf{s}:\mathbf{s}.
\label{eq:second-invariant}
\end{equation}

The corresponding von Mises equivalent stress is

\begin{equation}
\sigma_{\mathrm{eq}}
=
\sqrt{3J_2}
=
\sqrt{\frac{3}{2}\mathbf{s}:\mathbf{s}}.
\label{eq:von-mises-stress}
\end{equation}

In conventional notation, the yield function may therefore be written as

\begin{equation}
\Phi = \sqrt{\frac{3}{2}}f
=
\sigma_{\mathrm{eq}}
-
\sigma_f,
\label{eq:yield-function}
\end{equation}

where the current flow stress for linear isotropic hardening is

\begin{equation}
\sigma_f
=
\sigma_y
+
H\alpha.
\label{eq:flow-stress}
\end{equation}

Here, $\sigma_y$ denotes the initial yield stress, $H$ is the isotropic 
hardening modulus, and $\alpha$ is the accumulated hardening variable \cite{Borja2013,SimoHughes2006}.

When

\[
f\leq0,
\]

the stress state is elastically admissible. When

\[
f>0,
\]

the trial stress lies outside the expanded yield surface and a plastic 
correction is required.

An important feature of J2 plasticity is that the yield criterion does not 
depend on hydrostatic stress \cite{Borja2013,SimoHughes2006}. Consequently, two material points with 
identical deviatoric stress but different mean stresses reach yielding at the 
same value of $\sigma_{\mathrm{eq}}$. This assumption is appropriate for 
describing plastic flow in dense ductile metals. However, it is insufficient 
for describing ductile fracture because the nucleation and growth of cavities 
are strongly affected by hydrostatic stress \cite{Kachanov1986,LemaitreChaboche2004,CaleyronEtAl2012}.

The present formulation therefore separates the two physical mechanisms: J2 plasticity governs irreversible plastic flow, whereas the damage model 
introduces hydrostatic-stress sensitivity into the failure process. This 
separation allows the existing J2 constitutive model to be retained while 
extending the formulation toward ductile fracture.

### Equivalent Plastic Strain and Hardening

The tensorial plastic deformation generated by the J2 model is represented by a scalar accumulated equivalent plastic strain, 
$\bar{\varepsilon}^{p}$, which provides a convenient history variable for isotropic hardening and ductile damage \cite{Borja2013,SimoHughes2006}.

For associative J2 plasticity, the equivalent plastic strain rate is 
conventionally defined as

\begin{equation}
\dot{\bar{\varepsilon}}^{p}
=
\sqrt{
\frac{2}{3}
\dot{\boldsymbol{\varepsilon}}^{p}
:
\dot{\boldsymbol{\varepsilon}}^{p}
},
\label{eq:equivalent-plastic-strain-rate}
\end{equation}

or, in incremental form,

\begin{equation}
\Delta\bar{\varepsilon}^{p}
=
\sqrt{
\frac{2}{3}
\Delta\boldsymbol{\varepsilon}^{p}
:
\Delta\boldsymbol{\varepsilon}^{p}
}.
\label{eq:incremental-equivalent-plastic-strain}
\end{equation}

In the present implementation, the J2 plasticity model already contains an 
evolving scalar state variable named HardeningFactor}. This variable 
is used as the accumulated plastic-history measure,

\begin{equation}
\alpha
\equiv
\bar{\varepsilon}^{p}.
\label{eq:CoxPnull}
\end{equation}

The current yield stress consequently evolves according to

\begin{equation}
\sigma_f
=
\sigma_y
+
H\bar{\varepsilon}^{p}.
\label{eq:CoxPnull}
\end{equation}

This identification is particularly advantageous for the damage extension 
introduced in this work. No independent plastic-history variable needs to be 
introduced specifically for the damage model. Instead, the accumulated 
equivalent plastic strain generated by the existing J2 algorithm is directly 
reused as the principal history variable governing damage initiation and 
damage evolution. This provides a natural connection between the original J2 
constitutive formulation \cite{ZhangEtAl2025} and the ductile damage formulation of Caleyron et al.~\cite{CaleyronEtAl2012}.

For a plastic trial state, the scalar plastic correction used in the current 
implementation is related to the excess of the trial yield function. The 
corresponding correction measure is

\begin{equation}
\Delta\lambda
=
\frac{1}{2}
\frac{f_{\mathrm{trial}}}
{G+H/3},
\qquad
f_{\mathrm{trial}}>0,
\label{eq:CoxPnull}
\end{equation}

from which the equivalent plastic-strain increment can be expressed as

\begin{equation}
\Delta\bar{\varepsilon}^{p}
=
\sqrt{\frac{2}{3}}
\Delta\lambda.
\label{eq:CoxPnull}
\end{equation}

The accumulated plastic strain is then updated according to

\begin{equation}
\bar{\varepsilon}^{p}_{n+1}
=
\bar{\varepsilon}^{p}_{n}
+
\Delta\bar{\varepsilon}^{p}.
\label{eq:CoxPnull}
\end{equation}

Because

\[
\Delta\bar{\varepsilon}^{p}\geq0,
\]

the equivalent plastic strain is an irreversible history variable.

This relationship establishes the first direct connection between plasticity 
and damage in the present model:

\begin{equation}
\boxed{
J_2\ \text{plastic flow}
\longrightarrow
\Delta\bar{\varepsilon}^{p}
\longrightarrow
\bar{\varepsilon}^{p}
\longrightarrow
\text{damage evolution}
}
\label{eq:CoxPnull}
\end{equation}

Thus, damage is not driven by an independently prescribed geometric separation 
criterion. Instead, it develops from the accumulated irreversible plastic 
deformation already calculated by the underlying J2 constitutive model. This 
concept is consistent with continuum ductile-damage formulations in which 
plastic strain acts as an internal driving variable for the accumulation of 
material defects \cite{Kachanov1986,LemaitreChaboche2004,CaleyronEtAl2012}.